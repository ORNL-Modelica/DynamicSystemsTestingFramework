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
| 14 | Link to reference JSON file from HTML | L | Medium | Artifacts link to sim files but not the ref JSON; filename shown as text in ref_info but not clickable **Built**: the dashboard's Reference column and the per-test page's Reference Info link the reference JSON (with `--report`). |
| 15 | WebGL for large traces (scattergl) | L | High | Switch to `scattergl` when trace >5k points; SVG unusable beyond ~10k |
| 16 | ~~LTTB data decimation~~ | M | High | **DONE** — shipped in Phase 6.0 as Python-side decimation on the HTML embed (`reporting/decimate.py`). Applied at `Config.max_embedded_samples` (default 1000) to the `TRAJECTORIES` embed; `comparison_data.json` stays full-res. Browser-side LTTB on zoom is idea #48 (lazy-fetch). |
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
| 32 | Parallelize report generation | M | Medium | `--report` is sequential matplotlib/Jinja2 per test; embarrassingly parallel **Built**: `--parallel N` renders the report suite on N threads (`compare --report --parallel N`). |
| 33 | ~~Batch actions from HTML report~~ | M | High | **DONE** — checkbox column + action panel on index page. Bulk selectors (+ Failed/Sim Failed/No Baseline/Warnings/Stale), copy filter list, download selected.txt, copy run command. Live command preview |
| 34 | ~~NRMSE panel annotations + metric clarity~~ | L | Medium | **DONE** — avg-NRMSE line + shaded fail zone (above tolerance) on both matplotlib and Plotly NRMSE panels; "Pass/Fail Criterion" column added to variable table (mode-aware: NRMSE-vs-tolerance, %-inside-tube, or final-error-vs-tolerance) |
| 35 | ~~Filter by test list file~~ | L | Medium | **DONE** — `--filter` accepts glob, comma-separated list, or `@file` (one pattern per line, `#` comments) |
| 36 | Cross-platform reference comparison | H | High | Compare results across OS/simulator versions; use alternate ref as baseline |
| 37 | ~~Per-test report section reordering~~ | L | Low | **DONE** — Sim Params → Statistics → Diagnostics now sit above Variables in both `comparison.html` and `interactive.html`; structural warnings rendered as prominent yellow box near top |
| 38 | ~~Incremental run + report workflow~~ | M | High | **DONE** — persistent test_keys (`assign_test_keys` in `simulators/base.py`); `--merge` expands report scope to all known tests; `--rerun [CATEGORIES]` selects tests by prior status (default: failed; implies --merge); `last_run_at` shown in index column (relative time + tooltip) and per-test report header; stale rows greyed out |
| 39 | Live log tailing for per-test progress | M | Medium | Inside a Dymola batch, individual test transitions are invisible. Tail stdout/translation_log.txt and parse "Translating ModelX" / "Integration terminated" markers to flip dashboard status mid-batch. Per-backend log-marker hook |
| 40 | ~~Persistent Dymola workers with dynamic dispatch~~ | H | High | **DONE** — now the default (`--batch` inverse escape hatch). `DymolaInterface` auto-discovered (`.whl`/`.egg`), launches parallelized via `dymola_lock` patch, PID-tracked via `._dymola_process.pid`. Per-test timeout watchdog with disk-check rescue (trusts dsfinal.txt + reached stop_time). Worker restart (cap 3). Per-phase timing breakdown (translate/sim/other/total, rounded to 2 decimals), phase visible live on dashboard. Noise suppression during kills via `DymolaLogger` monkey-patch. `check-dymola` diagnoses loader |
| 41 | Dashboard refinements | L | Low | Filter buttons on dashboard (failed only, running only), keyboard-jump to next failure, dark mode. Index page got sortable columns + timing columns — dashboard could match **Built in part**: the dashboard's count pills are its status filter, and dark mode is `"theme": "dark"` in `testing.json` (D148); keyboard-jump is not built. |
| 42 | Quick-save selected results to reference | M | High | CLI: `accept --filter X` reads existing work_dir results and writes to reference, no resim. HTML report: "Copy accept command" button next to "Copy rerun command". Pairs with Phase 1.7 multi-baseline (accept writes to `primary` baseline by default; `--baseline <name>` overrides) **Built**: `dstf compare --accept [--filter X]` stores the last run's results without re-simulating, and the dashboard's Command menu builds that command for a selection; `dstf author`'s Accept does it for the checked tests. |
| 43 | Per-test timeout + other per-test knobs in test_spec | L | Medium | Extend `test_spec.json` to allow per-test `timeout`, `batch_size_hint`, etc. Currently only simulation/comparison settings are per-test. Show in per-test report (Sim Params section). Belongs in same schema expansion as MetricTree **Built** for the timeout: `simulation.timeout` in a `test_spec.json` entry. |
| 44 | Dymola-interface resilience tracking | M | Medium | Port-bind race on worker startup (~1/20 fails), "Mismatch request/response ID in JSON-RPC call", "Remote end closed connection without response". Existing worker-restart rescues most. Could add: post-port-find `SO_REUSEADDR`+bind-probe before returning, broader noise-pattern filter, exponential-backoff retry on RPC mismatch |
| 45 | Python-driven tests (user-code backend) | H | High | New sibling backend `CustomPythonRunner` + `PythonTestRecognizer` for `.py` test files. Contract = `run(context) -> SimulationResult` dataclass (time, variables, diagnostics, metadata); framework owns persistence. User implementation is free — fmpy-with-custom-inputs, pyomo, scipy.integrate, custom solvers, CSV loaders — framework only enforces the return shape. Prerequisite: refactor `FmpyRunner` to produce `SimulationResult` first, proving the contract on the existing case. Single backend per testing.json stays the pattern (no per-test dispatch). Post Phase-6-MVP; pairs with the deferred D65 FMU-path semantic-gap closure. **Built** (D80) as `PythonBackend`: a subprocess per test calling the user's `simulate(stop_time, tolerance) -> dict`; tests come from `test_spec.json`, with no `.py` recognizer. |
| 46 | ~~Time-windowed leaf metrics~~ | M | High | **DONE** (A-tier close-out). Backend (6.1.1) + UI (A1) both shipped. `render_window_controls_html` emits universal start/end inputs for tree-backed leaves; `collect_leaf_paths` produces JSON-Pointer leaf paths; `buildPatchData` round-trips via RFC 6902 `add`/`remove` on `<leaf_path>/window`. Deferred stretch: range-brush on the trajectory plot (scalar inputs are v1). |
| 47 | Time-array dedup in interactive.html (6.0.1) | M | High | Every variable currently embeds `act_time` + `ref_time` per trajectory — within a single test these are shared across all variables, so a 50-var test embeds 100 redundant time arrays. Dedup = emit one shared pair per test + reference-by-index per variable; compounds ~50% payload reduction on top of LTTB decimation. Would raise the 6.0 cap from 1000 back to 2000 under the same 5 MB budget. Touches template JS at 6+ call sites (`TRAJECTORIES[idx].act_time`) — coordinated Python + Jinja + JS change. |
| 48 | Lazy-fetch full-res on zoom (6.0.2) | M | High | Tier-2 of the payload strategy: JS detects zoom events via `plotly_relayout`, fetches `comparison_data.json` (full-resolution, already on disk next to the HTML), slices to the visible x-window, rerenders the window at native fidelity. Works from `file://` URLs — no server needed. Restores full visual fidelity for users who actually need it without inflating the standalone-HTML payload. |
| 49 | Per-test max_embedded_samples override (6.0.3) | L | Medium | Extend `test_spec.json` `comparison` block with an optional `max_embedded_samples` field — escape hatch for tests with pathological signals (stiff ringing, sharp events) that legitimately need higher embedded fidelity than the global cap. Per-test resolution order same as tolerance (variable_override → test → config → default). Small, additive; can land anytime. |
| 50 | ~~Companion + soft_check overlay rendering (6.3 slice)~~ | M | High | **DONE** (A-tier close-out). `reporting/overlay_loader.py` loads JSON + wide-CSV companions (graceful degradation on missing/invalid), plus soft_checks. Per-plot picker defaults off; test-level summary lists unrendered overlays. Soft_checks render purple dotted, companions green dashdot; LTTB decimation integrated. |
| 51 | ~~Multi-simulator sibling-result overlays as companions~~ | M | High | **DONE (partial)** — sibling-backend auto-companion overlays shipped in commit `d3d6cfb` (post-D69 follow-up). `load_overlays` auto-scans `<reference_root>/<other_backend>/<os>/ref_*.json` for the same `model_id` at report time; renders as `kind="sibling-backend"` blue-dashed overlays with per-plot picker. D71 (2026-04-23) extended picker UI to the no-baseline code path so fresh-backend workflows see the same toggle UX. **Still open**: (a) user-labelled overlay names (currently `sibling_dymola` / `sibling_fmu`); (b) opt-in `"auto_companions"` config knob to restrict which sibling roots are scanned (all sibling backends auto-discovered today). |
| 52 | ~~Wrap-in-combinator + combinator-kind editing in reporter~~ | L | Medium | **DONE** (D72, 2026-04-23). Kind dropdown in combinator header (5 options); ⊕ wrap button on every node; ⊖ unwrap on single-child combinators. Seeded defaults for k-of-n / weighted. No new patch ops — wholesale `/metrics` replace envelope. +17 Playwright tests. Multi-select wrap + live validation halos deferred. |
| 53 | `check-openmodelica` CLI subcommand | L | Low | Peer of `check-dymola`. Verifies omc on PATH + MSL installed + OMPython importable + omc version. Prints diagnostic table. Useful onboarding for new users, especially on machines where `installPackage(Modelica)` hasn't been run. ~½ day. D70 deferred. **Built**: `dstf check-openmodelica` (and `dstf check-julia`). |
| 54 | OM FMU export via `buildModelFMU` | M | Medium | Wire OpenModelica into the `Capability.FMU_EXPORT` cross-backend chain. Currently Dymola-only (and experimental per D65). Would let the D63 `produce_dymola_via_fmpy_baseline` chain reciprocate — `produce_openmodelica_via_fmpy_baseline` — for cross-backend regression on OM-authored models. ~1–2 days. D69 deferred. |
| 55 | ~~Julia ModelingToolkit backend~~ | H | High | **DONE** (D77 + D78 + D79). Fourth `SimulatorRunner` shipped. Subprocess-per-test batch path (D77) + persistent-worker stdin/stdout JSON protocol (D78) + JuliaMtkTestingLib with 7 tests (D79). `examples/julia/JuliaMtkTestingLib/Examples/*.jl` — each exports `build_mtk_system()` → `(sys, u0, ps)`. Framework driver (`run_test.jl` / `run_persistent.jl`) invokes `structural_simplify` + `ODEProblem` + `solve`, writes JSON result. Observables + unknowns both materialized. 14 cross-library companions wired (both directions) between JuliaMtkTestingLib and ModelicaTestingLib via portable relative paths. Dyad still untested — should work via the same MTK path (Dyad compiles to MTK). `SimulationResult` refactor never needed; `TestResult` in `simulators/base.py` was already the shared typed return.<br><br>**Still open follow-ons**: EventTest / IntervalTest / NoUnitTest / SimulateOnlyTest Julia ports; MTK FMU export via `ModelingToolkit.generate_fmu`; Dyad validation sample; JuliaCall-based embedded persistent path (probably not needed — subprocess is fast enough). |
| 56 | Declared-peaks provenance UI polish | L | Low | D76 stamps `derived_from_window: {start, end}` on peaks added via Detect or Shift+click. Could extend: hover tooltip on the peak marker on the spectrum subplot (not just the table row); "Set window from provenance" button per peak row to snap the leaf's current window to that peak's derivation window. Small ergonomic wins; ship when someone asks. |
| 57 | Experiment-data alignment preprocessing | M | Medium | Time-offset / amplitude-scale alignment for CSV baselines before scoring. Belongs in the comparison layer as a preprocessing wrapper or new ComparisonMode. Wait for concrete user demand — D66 says calibration-adjacent work belongs downstream. |
| 58 | Persistent-worker Python (PythonCall / stdin-JSON) | M | Low | Long-lived Python subprocess with stdin-dispatch, like JuliaPersistentRunner. Per-test startup is ~30-100 ms today; pays off once a suite has hundreds of Python tests. Mirror the D77→D78 Julia progression. |
| 59 | ~~Baseline-free modes short-circuit NO_REF~~ | L | Low | **DONE** (D83, 2026-04-24). New `ComparisonMode.is_baseline_free()` on the base (default False); overridden True on `RangeMode`, `EventTimingMode` (iff `events` declared), `DominantFrequencyMode` (iff `peaks` declared). When every leaf in a test's tree is baseline-free AND the baseline is missing, `compare_all` now proceeds to `compare_test` (reference={}) instead of collapsing to NO_REF. Mixed trees stay NO_REF. +5 tests (791 → 796). |
| 60 | Extract shared "declared-items table editor" helper | L | Low | Dom-frequency's declared-peaks editor and event-timing's declared-events editor (D82) share ~74% of structure (table render, source dropdown, detect button, add/delete). Extract a factory: `createDeclaredItemsEditor({getItems, itemToRow, onAdd, onDetect, renderMatch})` — both editors become ~30-line call sites. Follow-up; YAGNI-deferred until the second editor shipped so the factory shape is concrete. |
| 61 | ~~Points editor — draggable plot markers (Full scope)~~ | M | Low | **DONE** — wired to the shared createPointPlotEditor factory. Shift+click on plot adds a point at (clicked_x, clicked_y) with explicit value; shift+drag on a diamond marker moves time + value (promoting ref-relative points to explicit-value); shift+right-click on a marker removes it. Same abstraction tube + dom-frequency use. |
| 62 | CLI subcommand parity + standardization | L | Medium | Subcommands grew organically and have inconsistent flag coverage. Concrete known gap: `dstf compare` has no `--parallel`, so the read-and-compare phase always runs serially even when the user has cores available; on a 100-test suite this is the dominant time cost when re-comparing without re-simulating. Other gaps probably exist (audit needed): `--work-dir` / `--timeout` / `--merge` / `--rerun` / `--accept` / `-i` / `--report` are scattered unevenly across `run` vs `compare` vs `manifest`. **Hard constraint from user: no code expansion — same or reduction in LOC**. Right approach is argparse parent parsers (`parents=[...]`) — define groups once (FilterGroup, ParallelismGroup, ReportGroup, etc.), each subcommand opts in to the groups it needs. Flag declarations dedupe across subcommands, the inevitable next subcommand inherits the canonical flag set. Extend `test_argparse_subcommands_match_dispatch_table` to also assert each subcommand declares the flags from its declared groups. ~½ day; net-negative LOC. |
| 63 | Failure-cluster summary in run report | L | Medium | Today the dashboard / per-test report shows each failure individually with its `error_message`. When the same root cause hits many tests (e.g. the TRANSFORM Dymola Linux run had 84 failures all with the new `"Translation failed: license tier too small for this model"` classification), users have to skim each row to notice the pattern. Idea: after the run, group failures by their classified `error_message`, and surface the top-N clusters in the index page header — e.g., `"242 passed, 84 failed (84 / 84 = 'license tier too small' — likely a Dymola license-tier issue, not test bugs)"`. Builds on the `_classify_translation_error` helper added in 790ddde; same dispatch could classify simulation failures too once we have their patterns. Discovered while running TRANSFORM on Dymola Linux 2026-04-29 — would have saved minutes of digging through individual translation_log.txt files to confirm the failures were all-the-same-cause. ~10 LOC in the report-suite generator. |
| 64 | Header-only ref scan on index build | L | Low | `ReferenceStore._scan` reads and `json.loads` every `ref_NNNN.json` in full just to pull `model_id` + `status` off the top. On a suite the size of TRANSFORM (~326 baselines, each carrying full trajectories) that is tens of MB parsed on every CLI invocation that touches the store. Options: a tiny sidecar index written on accept, or a streaming/partial parse that stops after the metadata keys (`json` has no partial-parse entry point, so this means a hand-rolled scan or `ijson`). Noticed during the D-7 docstring sweep (Phase C-ii) — the comment there claimed a first-few-KB read that the code never did. |

**Recommended order** (post-Phase-6-MVP, reorganized 2026-04-23):

- **A. Finish half-shipped features** (fully closed out as of D72):
  - ~~**#46 UI surfacing**~~ — DONE (A-tier close-out).
  - ~~**#50 companion + soft_check overlay rendering**~~ — DONE (A-tier close-out).
  - ~~**Sibling-backend overlays cross-path parity**~~ — DONE (D71, 2026-04-23). Picker UI now on NB code path too.
  - ~~**#52 wrap-in-combinator + combinator-kind editing**~~ — DONE (D72, 2026-04-23). Reporter-as-IDE story complete.

- **B. User-facing new features** (pick one — each 1–2+ weeks):
  - **Phase 7 rule-based recommender** (signal → tree proposals; bounded vocabulary; see D66).
  - **TRANSFORM upstream portability PR** — the 46 `each`-modifier fixes surfaced by the OM sweep. External PR, not a change to this repo. Lifts OM pass rate 72% → ~85%. ~1 day.
  - **#45 python-driven tests + FMU-path semantic-gap closure** (D65 follow-on) — share a `SimulationResult` dataclass refactor. Unlocks pyomo / scipy / custom solvers + real industrial FMU testing.

- **C. Performance / fidelity follow-ups** (nobody's blocked yet; ship opportunistically):
  - **#47 time-array dedup (6.0.1)** — compounds on 6.0; lifts default cap 1000 → 2000.
  - **#48 lazy-fetch on zoom (6.0.2)** — full-fidelity drill-down from decimated base.
  - **#49 per-test override (6.0.3)** — escape hatch; ship when someone asks.
  - **#53 `check-openmodelica` CLI subcommand** — trivial onboarding polish (~½ day).
  - **#54 OM FMU export via `buildModelFMU`** — wire OM into the cross-backend chain (~1–2 days).
  - Drag-to-edit range handles (6.1.4 stretch).

- **D. External-distribution blocker** (technical scope small; blocked on a name):
  - **Tool rename** — `"ModelicaTesting"` → neutral name. Touches package, CLI prog, HTML titles, `pyproject.toml`, all imports. More justified now: three backends, no Modelica coupling in the core pipeline. (Resolved in D81: now DSTF.)

- **E. New leaves + foundational** (additive, slot in anytime):
  - **Phase 9 dataset types** unlock #23 Fréchet, #24 spectral coherence, #26 ISO 18571, #25 x-tolerance/pyfunnel.
  - Small HTML polish: #7 variable ordering, #9 open-in-Dymola, #14 link to ref JSON, #17 linked panel zoom, #18 worst-violation annotation, #19 zoom-dependent stats.
  - Larger: #11 test-discovery helper, #12 model-health analysis, #15 WebGL scattergl.

**Immediate default** (post D79): A-tier + #55 Julia fully closed. Four backends, two test libraries, reporter-as-IDE feature-complete. **Recommended B-tier picks for the next session** — any of:
- **Phase 7 rule-based recommender** (~2-3 days skeleton, ~1-2 weeks full). New `recommender/` package. Lowers onboarding for new users.
- **TRANSFORM upstream portability PR** (~1 day external). 46 `each`-modifier fixes, lifts OM pass rate 72% → ~85%.
- **#45 Python-driven tests** (~2-3 days MVP). Natural pairing with the proven Julia subprocess+TestResult contract — template is essentially "replace `julia` with `python` in the D77 runner."

**Julia follow-ons** (C-tier, ship opportunistically): MTK FMU export, Dyad sample, port EventTest/IntervalTest/NoUnitTest/SimulateOnlyTest to Julia.

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
- Could be a CLI command like `dstf find-tests --extends Modelica.Icons.Example --package MyLib.Fluid`
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
- Could be a CLI command like `dstf analyze` or `dstf health` that produces a summary table sorted by severity
- Output as console table, CSV for spreadsheet analysis, or HTML dashboard
- Useful for library maintainers to prioritize optimization work across hundreds of models

## Link to reference JSON file from HTML report

- Artifacts section links to simulation files (dslog.txt, dsin.txt, dsres.mat, etc.) but not to the reference JSON file (e.g., `ref_0042.json`)
- The reference filename is already shown as text in the "Reference Information" table via `ref_info` (see `context_builder.py`, `_build_ref_info`) but it's not a clickable link
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
- Data already in template context: `max_abs_error`, `max_abs_error_time` (built in `context_builder.py`); for tube mode: `tube_worst_violation`, `tube_worst_violation_time`
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
- Output: generate a ready-to-paste CLI command (e.g., `dstf run --filter Model1,Model2,Model3`) or a JSON payload
- Lighter than the full server approach (#29) — purely client-side JS that builds command strings
- Could also generate a filter file compatible with `--filter @tests.txt` (#35)
- "Mark obsolete/disable" would need a status field in test_spec or reference JSON — design this carefully to avoid scope creep

## NRMSE panel annotations + metric clarity

- On the per-variable NRMSE error plot:
  - Vertical dashed line at the time of max absolute error (data already in `max_abs_error_time`)
  - Horizontal line for average NRMSE across the trajectory
  - Horizontal line at the tolerance threshold (data already in `tolerance_used`)
  - Shaded region above tolerance to visually show "fail zone"
- **Metric clarity**: clearly label which metric drives pass/fail — currently NRMSE for trajectory mode, fraction-inside for tube mode, relative error for points
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
- **Proposed CLI**: `dstf accept --filter X` — reads `work_dir` artefacts and writes to reference, no simulation. Mirrors the `run --filter` UX exactly. Accepts `@file` and glob syntax.
- **HTML report**: add a "Copy accept command" button next to "Copy rerun command" on the index page. Uses the same row-selection UI. Generated command shape: `dstf [--config ...] accept --filter "Model1,Model2"` (or `@selected.txt` for >3).
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
  - *Remote end closed*: `Remote end closed connection without response.` Dymola subprocess died or the HTTP channel dropped. Not printed for a Dymola a worker closed itself (the log filter mutes a closed interface's connection errors); one that died by itself prints it until the watchdog finds the process gone and closes the worker.
- **Possible mitigations**:
  - *Port race*: after `_find_available_port`, try a brief `socket.bind(port)` probe inside the lock; if it fails, loop to next candidate. Closes the find-to-bind window.
  - *RPC mismatch*: on this exception, force a worker restart rather than propagating (one-shot retry at the test level). Existing watchdog already does this via timeout — make the exception path symmetric.
  - *Remote end closed*: treat as worker death and restart — done where the process is gone (found dead before a test: restarted first; died during one: that test fails and the next restarts it).
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

- **Motivation**: Many regression criteria are naturally piecewise — NRMSE matters during the steady-state tail, tube matters during the transient, points is window-irrelevant. Today users either accept a single global score or hand-author overlapping leaves that each recompute on the full trajectory. A time window is the missing axis.
- **Proposal**: A uniform `"window": {"start": <t>, "end": <t>}` field on every leaf (optional; defaults to full trajectory). Evaluated by slicing the actual + reference trajectories to the window before handing off to the existing `ComparisonMode`. Both endpoints optional — open-ended on either side supported.
- **Architecture fit**: One change in `comparison/tree_eval.py` — slice inputs before the `ComparisonMode.compare(...)` call. No change to the six compute modes. Leaves that already don't use time (range) treat the field as a no-op.
- **Not a combinator**: deliberately *not* a new AND-with-time-bounds combinator — that would explode the grammar. The window is a leaf property, scoped narrowly; AND/OR above still compose as today.
- **Composition**: NRMSE-on-[0,10] + NRMSE-on-[10,100] + tube-on-[10,50] under a root AND gives a piecewise regression contract. Same grammar, richer expressiveness.
- **Integration with Phase 6**:
  - **6.1 ripple**: every auto-derived UI panel gains two number inputs (`start`, `end`). Cheap — two fields, one new validator (`end > start`, both in trajectory range). Adds maybe ½ day to 6.1.1/6.1.2.
  - **Live preview**: the JS port slices the already-cached arrays; trivial for nrmse/tube/range/points. No new recompute logic.
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
- **Scope & risk**: touches `reporting/context_builder.py` (trajectory-building) + `reporting/templates/interactive.html` (template structure + JS references at ~6 call sites: lines ~461, 713, 757, 904, 1082, 1307 of interactive.html). Coordinated Python + Jinja + JS change — non-trivial but contained.
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

## Companion + soft_check overlay rendering — 6.3 first slice (#50)

- **Motivation**: Phase 6 MVP shipped the baseline-role split in full (primary / companion / soft_check) — on disk, in the CLI (`companion add/list/freeze/remove`, `soft-check list/remove`, `import-baseline`), and in the validator. But the **reporter only plots the primary**. Companions persist as pointers but aren't loaded or rendered; soft_checks are scored against (via `against:` inside `warn`) but aren't visually overlaid either. A user who runs `companion add BouncingBall rig ./rig_data.csv` sees nothing in the HTML report. That's the part of D66's three-role model we haven't delivered yet.
- **Proposal**:
  - **Python side** (new module `reporting/overlay_loader.py`): `load_companion(companion: Companion) -> Optional[TrajectoryOverlay]` — reads CSV (with autodetected `time,value` / `t,y` / first-two-columns-are-time-and-value heuristic) or JSON (reuse `Baseline` shape), returns a `TrajectoryOverlay` dataclass with `{name, kind: "companion"|"soft_check", time, values, origin_path}`. Graceful degradation: file missing / malformed → log warning, return None (per D66 — reporter never crashes on a bad companion).
  - **Context builder** (`_build_template_context`): gather `overlays: list[TrajectoryOverlay]` per variable (map by variable name), attach to each trajectory dict.
  - **Template + JS**: overlays render as additional Plotly traces on the same plot — distinct colors, `legendgroup` collapsible. A small picker in the plot-section header toggles their visibility; state lives in `perVarOverlayVisibility[idx]`. Default visibility = primary + actual always on; companions + soft_checks default OFF to avoid clutter, user toggles on. Legend names include role prefix (`experiment (companion)`, `dymola-via-fmpy (soft_check)`).
- **Scope explicitly NOT included** (remains in 6.3 proper):
  - Tree-level authoring controls (6.2).
  - Edit/view toggle (6.5).
  - Draft-tree preview (6.6).
- **Companion CSV heuristic**: the format is intentionally loose (rig export formats vary wildly). Safe defaults: first column is time, remaining columns are per-variable values named from the header row. Ambiguous cases log a warning and skip the companion rather than guessing wrong.
- **Graceful-degradation guarantee** (D66 invariant): if a companion's data file is moved, deleted, or unreadable, the plot still renders with everything else intact — only that companion's trace is absent, and the picker marks it unavailable.
- **Tests**: `test_overlay_loader.py` — CSV parsing (happy path, malformed row, missing file); JSON parsing; reporter context includes overlay entries when companions/soft_checks exist on a model. Refresh goldens for the two affected template branches (variable-table cell unchanged, per-plot section gains the picker element).
- **When**: A-tier next step alongside #46 UI surfacing. Naturally ships as a single PR with #46 — both extend `reporting/ui/mode_controls.py` and the per-variable template section; their golden-hash changes overlap.

## D80 follow-ups (Python-driven tests)

- **Experiment-data alignment** (ideas-matrix N+1): The current
  ConstantCsv example requires the user's CSV to already be correctly
  sampled and aligned. Real-world experiment data often needs
  time-offset alignment (cross-correlation / min-NRMSE over shifts),
  amplitude scaling, clock-drift re-sampling, or steady-state window
  selection. Belongs in the comparison layer as either a new
  `ComparisonMode` that wraps a base mode with preprocessing, or as
  a generic preprocessing hook on the MetricTree. Explicit
  non-goal: parameter estimation or calibration (D66 → downstream
  tools). Wait for a concrete user demand with specific alignment
  requirements before building — the generic machinery risks fitting
  nobody.

- **Dynamic data fetching** (no framework change needed): users
  whose test data lives on a REST API, S3 bucket, time-series
  database, etc. can already implement this in their `.py` file
  today. The `simulate(stop_time, tolerance)` function can do
  arbitrary I/O — fetch the data, filter it, return the trajectory.
  Framework-level "data-source" plug-ins would be premature; revisit
  only if multiple users converge on the same data-source pattern.

- **Persistent-worker Python** (ideas-matrix N+2): the batch-only
  MVP pays ~30-100 ms per test in subprocess + importlib startup.
  For small suites this is invisible; for 100+ Python tests it
  becomes the dominant cost. The Julia D77→D78 progression is the
  template — long-lived subprocess + stdin-JSON dispatch, same
  contract on the wire.

- **Julia-in-CI executor** (2026-07-06, review wave): the D89 .jl driver
  guards (retcode/final-time check, stale-definition poison sentinel,
  json_escape) are text-pinned only — no Julia on the dev box. A tiny CI
  job with the official julia container + OrdinaryDiffEq could actually
  execute run_test.jl/run_persistent.jl against a 2-state toy system and
  retire that standing residual.

- **Known-drift baseline marking** (2026-07-06): the TRANSFORM
  verification left 5 real-drift failures pending adjudication. An
  `expected_fail: "<reason/issue>"` field on a test-spec entry (surfaced
  as XFAIL in console/JUnit, distinct from PASS/FAIL) would let a suite
  stay green-gated while drift is investigated, without re-accepting
  baselines prematurely or muting the check.

- **`compare --accept`** — DONE 2026-07-08 (D93); status.json verdict
  guard closed the footgun without the manifest change. Original note:
  (2026-07-07)
  today `--accept` lives only on `run`, so accepting the results you just
  ran means re-simulating the whole suite (a ~44-min round-trip on
  TRANSFORM). Add `--accept` to `compare` so it stores the last run's
  on-disk results as baselines directly. Blocked on the same gap as the
  D92-deferred note: `BatchManifest.save()` doesn't persist per-test
  success flags, so `compare` currently can't reliably tell a genuine
  result from a stale/partial `.mat`. Fix that first (persist run results
  in the manifest), then `compare --accept` and honest SIM_FAIL-on-compare
  both fall out of it. Requested after the TRANSFORM adjudication where
  re-running to accept was the only path.

- **BUG to investigate — rerun-command selection leaks across reports**
  (2026-07-07, owner-reported): selecting tests in a report to build the
  "rerun these" prompt text at the bottom sometimes includes tests that
  are NOT the current sheet's selection — it appears to hold over selection
  state from a previously-open report (dashboard vs a per-test interactive,
  or a prior dashboard). Suspect shared/persisted selection state
  (localStorage? a module-global set not scoped per report/page?) in the
  dashboard/interactive JS rerun-command builder. Repro to capture: open
  report A, select some tests, open report B, select different tests, read
  the rerun text — check whether A's selection bleeds in. Fix: scope the
  selection set to the current page/report identity and clear it on load.

- **`export-script` — emit a single test as an IDE-runnable script**
  (2026-07-08): when a test fails, drop into the Modelica IDE to poke at it
  by hand — `dstf export-script <model>` writes a `.mos` (Dymola) / OMEdit
  snippet that opens + simulates just that model with the test's resolved
  experiment params (stop_time/tolerance/method/interval), so the
  investigation matches what DSTF ran. Bridges DSTF → the interactive
  `dymola-sim-debug` loop. Inspired by the old buildingspy flow's generated
  `runAll_Dymola.mos` (retired in the TRANSFORM-UnitTests cleanup). Small,
  backend-scoped (Dymola/OM first). Not the "run everything in the IDE"
  convenience — the useful modern version is "hand me THIS failing test."

- **Elevate #25 (funnel / X-direction time tolerance)** (2026-07-08): the
  TRANSFORM 2026x adjudication showed drift that is partly *solver-timing*
  sensitive — IRIS results are OS-dependent (linux 0.0351 vs windows 0.0535
  for the same model/version) and event instants shift between toolchains.
  DSTF's `tube` is Y-only and cannot absorb a legitimate time-shift; a real
  2-D funnel (dx AND dy envelope, à la LBL `funnel`/pyfunnel — which the old
  buildingspy flow used, see the retired `funnel_comp/`) can. This makes #25
  more valuable than its current M/High ranking: it would have let the pump/
  IRIS drifts be judged on shape rather than failing on a small timing offset.

- **Report → CLI action menu (not just rerun)** (2026-07-08, owner): when you
  select tests in the HTML report, the "copy command" affordance only builds a
  rerun. Offer the full set scoped to the selection — `run`, `compare`,
  `compare --accept` (D93), `--report`, `--rerun <category>` — as copy-to-
  clipboard commands so the report is a launchpad into the CLI. Pairs with the
  #42/D93 accept work and the rerun-selection-leak bug (scope the selection set
  per report first). Small JS; the command strings already exist server-side.

- **In-report glossary / help lookup** (2026-07-08, owner): make the report a
  place to get oriented, not just read verdicts. A glossary panel / inline
  lookup explaining the vocabulary — NRMSE, tube/band/rel/abs, points,
  event-timing, dominant-frequency, PASS vs FAIL vs SIM_FAIL vs NO_REF,
  primary/soft_check/companion baseline roles, warn-wrapping. Content largely
  exists in docs/usage.md + the mode field metadata (label/help already flow
  into the UI via mode_controls); surface it as a "?"-panel keyed by the mode
  each variable uses.

- **Richer button hover hints** (2026-07-08, owner): descriptive `title`/
  tooltip text on the report's buttons (accept/rerun/copy, tube/point editors,
  window controls, per-leaf scoring toggles) so hovering explains what each
  does. Cheap, high-orientation-value; complements the glossary above.

- **Report screenshot in the README** (2026-07-08, owner): embed a rendered
  dashboard/interactive screenshot in the DSTF README so the "reporter-as-IDE"
  story is visible at a glance. Generate from a demo (ModelicaTestingLib /
  python example) so it's reproducible; commit the PNG under docs/img/. Doable
  now.

- **CI-status badge on the README** (2026-07-08, owner): the repo has the
  `quality` GitHub Action (ORNL-Modelica/DynamicSystemsTestingFramework); add
  its passing badge to the DSTF README top. Doable now (see this session).

## Derived variables + one shared expression facility (owner, 2026-08-31)

- **Motivation**: owner asked whether criteria could cover computed quantities
  (sums, differences) over both scalars and time series, not just raw signals.
- **Shape**: ONE small evaluation facility serving two consumers: derived
  *series* (spec-declared, e.g. `"err": "a - b"`, computed on results before
  scoring, fed to existing metrics) and derived *scalars* (fed to the scalar
  leaf). Safe evaluator (AST whitelist / numexpr-style), no bare eval.
- **Explicitly deferred**: the v1 scalar leaf ships dotted stat paths +
  closed-set series reductions (max/min/mean/sample_mean/final/integral —
  the last added by D109) only; its
  quantity field is structured so an `{"expr": ...}` variant can be added
  without a format break. Nothing in v1 blocks on this.
- **Today's outlets** (why this can wait): declare the derived quantity in the
  model (`Real err = a - b;`), compute it in a Python-backend `simulate()`,
  or attach it as a companion.

- **Tree-path config errors should be labeled leaf failures, not crashes** —
  DONE 2026-08-31 (D107); `_evaluate_leaf` now catches `ValueError` from both
  `resolve_mode` and `mode.compare` and returns a hard-fail sentinel leaf.
  Original note:
  (2026-08-31, review watch-only from D106): a `metrics` tree leaf whose mode
  config can't build — `scalar` without `quantity`, `range` without bounds —
  raises `ValueError` out of `resolve_mode` inside `tree_eval._evaluate_leaf`
  and aborts `compare_test`, while the flat `variable_overrides` path catches
  the same error and emits an error VariableComparison. Give the tree path the
  same catch (hard-fail sentinel with `diagnostics["error"]`) so a spec typo
  fails one leaf loudly instead of the whole comparison.

- **Dashboard row: unscored-leaf hint for the mixed case** (2026-09-01,
  from the D121 screenshot pass): a test with n_unscored > 0 AND at
  least one finite leaf score shows only the bare worst-score number on
  the dashboard row — the `n/a (N unscored)` branch fires only when NO
  leaf scored. The per-test key stat discloses it ("(1 leaf
  unscored)"); the row could append a small "(+N unscored)" marker.
  Cosmetic; the honest-display work of D121 covers the all-unscored
  case already.

- **Two-tier split: a reporting tier that mutates nothing, and a server-based
  authoring tier** (2026-09-04, owner): the authoring layer is written in
  JavaScript and therefore duplicates the Python — 6,170 lines under
  `reporting/templates/`, of which roughly 87% (`editors.js`, `tree-ui.js`,
  `export.js`, plus `scorers.js` which exists only to give authoring live
  feedback) serve authoring, against ~808 lines of actual reporting, guarded by
  a 1,350-line parity suite. (Correction, 2026-09-08: an earlier version of
  this entry claimed three renderers, with matplotlib in `cli/review.py` and
  `report_suite.py`. No `import matplotlib` exists under `src/`; those two
  are callers of the one Plotly/JS renderer, and the duplication is
  Python-versus-JavaScript, three JS registries deep. `matplotlib` is a dead
  dependency. Design: `docs/superpowers/specs/2026-09-08-two-tier-architecture-design.md`.)
  Target shape: one figure layer in Python that both tiers consume (the
  authoring tool's figures ARE the reporter's figures, one interactive and one
  not); the reporter plots bounds arrays Python already resolved, so no
  interpolation, geometry or scoring survives in the browser and
  `tests/test_scorer_parity.py` is deleted rather than maintained; everything
  that mutates a spec or launches work moves behind the RFC 6902 JSON-Patch
  contract `spec-update` already round-trips. This also gives a home to three
  things logged separately and half-built: #29 (optional server), #42
  (quick-save selected results without resimulating), and the report → CLI
  action-menu entry.
  The line is MUTATION, not interactivity: filtering, zoom, sweeping a
  selection to build a rerun command and copying values off a plot are viewing
  operations and stay in the reporter. They should be built once, on the shared
  figure layer, rather than against the path it replaces.
  Needs a spec before it can be reviewed. Sequenced after the tube fix and
  cubic tubes, before the funnel (#25).

  **The authoring tier is a front end, not a criteria editor** (owner,
  2026-09-04, and a REQUIREMENT on the spec rather than a wish): it must be
  structured so new capabilities attach without restructuring. The risk named is
  concrete — building it narrowly around the one job it replaces (editing tubes,
  points and peaks on a plot) and then rebuilding for every capability after
  that. Note the asymmetry in how it has been described so far: by what it
  REPLACES, not by what it must ACCOMMODATE. Those produce different designs, and
  the second is the one wanted.
  Candidates named, as evidence for the constraint rather than as v1 scope:
  - Selecting tests and rerunning them. Logged twice already (#42, and the
    report → CLI action menu) but both as copy-a-command affordances; with the
    Python underneath it becomes actual execution, which is a different feature
    wearing the same name.
  - Setting up a new library, and remembering which libraries and configs have
    been used — a local user config / recent-projects notion. Nothing in this
    file covers that today. It also re-values #10 (interactive setup wizard,
    rated Low on the grounds that power users skip a wizard): "a front end that
    knows your libraries" is a different proposition from "a wizard you run
    once".
  - Broader uncertainty quantification and test sweeps — flagged by the owner
    with a boundary concern rather than proposed. See below.
  Report REVIEW inside the tool (rather than only authoring) was raised the same
  day and deliberately kept out of v1: the design should not preclude it, and the
  cheap first step is a link to the existing HTML report rather than a second
  rendering of it. If it ever lands, the HTML report's audience narrows to CI,
  archiving and people without the tool — which is a cleaner definition than
  "light viewing for humans" but decides how much polish that report deserves, so
  it should not be discovered late.

- **Where does DSTF end and the next tool begin?** (owner, 2026-09-04, open
  question, not a work item): uncertainty quantification and test sweeps might
  belong to a SIBLING tool in a DSTF ecosystem rather than inside DSTF, the same
  way D66 puts calibration, root-cause analysis and parameter estimation
  downstream. The owner's framing, worth keeping because it is not re-derivable:
  building the authoring front end may be how that line gets FOUND — the
  capabilities that feel natural to attach to it are DSTF's, and the ones that
  feel like they want their own home are the next tool's. No decision is being
  taken here; the note exists so the question is not re-derived later.

- **Formal documentation site (Sphinx, code-doc-first)** (2026-09-02, owner):
  build DSTF's user and API documentation with a proper documentation tool
  such as Sphinx, relying on docstrings and code documentation as much as
  possible rather than hand-written duplicates. Motivation: external
  write-ups defer their details to "the documentation", which today is
  README + docs/*.md only. Constraint from the owner: rely on code
  documentation as much as possible.

- **Tube evaluation on the union of the reference and actual grids**
  (2026-09-02; PLANNED 2026-09-04, see
  `docs/superpowers/plans/2026-09-04-tube-union-grid.md`). Two things the
  original note did not know. First, the union grid is provably as strict as
  continuous checking, so no crossing solver is needed: between consecutive
  breakpoints neither curve has a knot, both are linear, and a linear
  difference peaks at an endpoint (checked over 20,000 random cases against a
  20,000-point dense scan — the dense scan never found more). Second, there is
  a SEPARATE bug in the same function: the band itself is resampled onto the
  reference grid, so a `tube_points` control point at a time the baseline does
  not sample is rounded away — declare an upper bound of 0.10 at t=1.5 with a
  baseline sampling 0,1,2,3 and the code scores against 0.25. Grid density
  cannot fix that one; the control-point times have to be on the evaluation
  grid. Both are fixed by one change. Original note:
  `_compare_tube` interpolates the
  actual trajectory onto `ref_time` and checks only those points, so an
  excursion contained entirely within one reference interval is invisible.
  Evaluate the band at every actual sample as well (interpolate the
  reference-defined bounds onto `act_time` and check both grids). Tube
  only; NRMSE stays on the reference grid, since adding samples changes the
  statistic's weighting. Small change in `comparison/algorithms.py`; the
  in-band fraction's denominator becomes the union grid.

- **Vocabulary cleanup pass on the code, before the public 1.0 announcement**
  — DONE 2026-09-03 (D123). Shipped as the Backend rename: `src/dstf/simulators/`
  → `src/dstf/backends/`, `SimulatorRunner` → `Backend`, `PersistentRunnerBase`
  → `PersistentBackendBase`, `testing.json`'s `"simulator"` / `"simulators"` /
  `"simulator_setup"` keys → `"backend"` / `"backends"` / `"backend_setup"`, and
  the `--simulator` / `--simulator-path` CLI flags → `--backend` /
  `--backend-path`. Tier (b) went through as the owner ruled — DSTF and
  TRANSFORM-UnitTests updated in the same pass, no alias and no migration
  layer — and the stored reference tree was unaffected, as the scope check
  below predicted.
  Original note:
  (2026-09-03, owner, from a design review): the documented vocabulary and
  the code's have drifted apart, and the documentation is what a new reader
  meets first. The code still says `simulator` where the architecture says
  **Backend** — `src/dstf/simulators/`, `class SimulatorRunner`,
  `PersistentRunnerBase`, and `testing.json`'s `"simulator"` / `"simulators"`
  keys — all residue from ModelicaTesting (D81 renamed the project, not the
  internals). A Backend is a role (test descriptor in, trajectories out); a
  simulator is an external tool, and `ConstantCsv` is a backend that is not
  one, which is the architecture's central claim. Owner's requirement: be
  certain the code is correctly cleaned up before the official 1.0 release,
  so a reader arriving from the docs meets the same words.
  Scope, in two tiers:
  (a) Free to rename — package `simulators/` → `backends/`, `SimulatorRunner`
      and the `Persistent*Runner` classes, internal variables and docstrings,
      `docs/extensibility.md` §3 and the `plugins.py` docstring (see also the
      E01a follow-up: those two still justify the single entry-point group
      with the backend+metrics pairing rather than the recognizer+backend one).
  (b) Coordinated change, not a refactor — `testing.json`'s `"simulator"` and
      `"simulators"` keys. TRANSFORM-UnitTests drives these (project stage
      block). **Owner's ruling, 2026-09-03: do it — DSTF and
      TRANSFORM-UnitTests are updated in the same pass.** The coordination is
      therefore the simultaneous edit, not a migration utility for third
      parties; a reader/writer split still applies within DSTF (accept the old
      key on read for the length of the pass if that makes the two-repo swap
      safer, but never write it).
      Scope check done 2026-09-03: the stored reference tree is NOT affected.
      `config.py:665` builds `reference_root / simulator_backend / os_name`,
      where `simulator_backend` is a *value* ("Dymola"), so the ~326 stored
      baselines keep their paths under any rename. Tier (b) is the two JSON
      keys and nothing else.
  First step is an audit, not an edit: census every `simulator` / `runner`
  identifier and string, split it into (a) and (b), and bring the count back
  before anything is renamed.

- **UI-vs-code parity audit, with the documented criteria as the yardstick**
  (2026-09-03, owner, from a TRANSFORM run): the owner opened a `scalar` leaf
  in the interactive report and found (a) the variable picker offers only the
  test's tracked variables, with no way to reach the stored statistics the
  scalar mode is documented to score (`CPUtime.final`,
  `translation.scalar_unknowns`), and (b) no choices at all for the reduction
  — no `mean`, `sample_mean`, `max`, `min`, `final`, `integral`.
  Cause, traced 2026-09-03 (read-only): `ScalarConfig.quantity` is typed
  `str | dict | None` (`comparison/modes.py:357`). `derive_schema` builds an
  enum control only from a `Literal`, and a multi-type optional union falls to
  `passthrough` (`reporting/ui/mode_controls.py:124`), which renders as a
  raw-JSON textarea (`:273`). So the six reduce names and the dotted-path form
  exist only inside the field's `help` string. The stored-statistics gap is
  separate: a statistic is not a tracked variable, and nothing feeds the run's
  available `statistics` / diagnostics keys into a picker.
  Second client, same cause: `derive_schema` also backs `export-schema`
  (`reporting/schema_export.py:145`), so a `passthrough` field means the
  exported JSON Schema cannot fully describe that leaf either — and that
  schema is the machine-readable contract the docs lean on ("the fields of a
  new leaf can be built from the schema alone"). One loose
  type degrades both the GUI and the contract, silently in both.
  The idea is the audit, not just this fix. `passthrough` is a *silent*
  degradation, so the gap class is invisible by construction: for every
  registered metric and combinator, list every field, flag every
  `passthrough`, and flag every field whose real domain is enumerable but is
  not typed `Literal`. Yardstick, per the owner: every documented criterion
  beyond the trajectory — reductions of a tracked series, stored statistics
  such as final CPU time, translation statistics made load-bearing, bounds
  vs. baseline-relative tolerance — must be reachable
  through the UI, not merely through hand-edited JSON.
  Fix directions (sketches, not decisions): give auto-derive a `choices` /
  domain metadata key it honors for non-`Literal` fields, or retype
  `quantity` as a discriminated shape; and thread the run's available
  statistics and diagnostics keys into the report context so the quantity
  picker can offer them alongside the tracked variables.

- **A richer demo test: several metric kinds on one interesting trajectory**
  (2026-09-03, owner, from a demo-library review): every demo test
  exercises one idea on a simple trajectory — `TubeToleranceTest` is a single
  exponential, `MetricTreeTest` a step response plus a lag, `EventTest` a
  staircase, `MultiFrequencyTest` a three-tone composite. Nothing in the
  library shows interesting *behaviour* and a rich metric combination at the
  same time: a trajectory with a transient, discrete events, and a steady
  state, scored by a tube on one window, event-timing across the
  discontinuities, a `scalar` on a translation statistic, and a companion
  overlay — one plot carrying the whole story. Wanted for a live demo rather
  than a static figure (owner's framing: "we can leave that to the demo"). Constraint: it must stay a demo library model, i.e. no MSL
  dependency at parse time, in the style of the existing `Examples`.

- **A reproducible figure/screenshot capture script** (2026-09-03, from a
  documentation review): the report screenshots used in DSTF's write-ups are
  hand-taken and nothing in this repo can reproduce them — no
  Playwright capture recipe in `tests/`, `scripts/` or `docs/`. A small script
  that opens a named test's `interactive.html`, opens a named leaf's editor,
  applies a browser zoom, crops to declared panels and writes a PNG at a
  chosen `deviceScaleFactor` would make documentation figures regenerable
  after any reporter change. Measured motivation: one such screenshot came
  out 2704x1528 (808 DPI at print column width) yet its editor labels
  rendered ~0.44 mm tall, because the capture was under-zoomed and
  over-wide — a defect a
  scripted capture with explicit zoom and crop would not reintroduce.
  The Playwright dependency already exists in the dev extra (the interactive
  HTML tests are gated on it).

- **Audit the visual / UI test system for completeness, and cut the
  human-in-the-loop pass** (2026-09-03, owner, alongside the UI-vs-code parity
  audit): make sure the reporter's UI is covered well enough that verifying a
  reporter change does not depend on a person opening a browser.
  Measured starting point, 2026-09-03 (read-only):
  * ~160 Playwright tests across seven files
    (`test_interactive_playwright.py` 91, `test_interactive_points.py` 28,
    `test_interactive_range_window.py` 14, `test_interactive_against_baselines.py`
    11, `test_interactive_event_timing.py` 8, `test_dashboard_selection_playwright.py`
    3, plus `test_scorer_parity.py`'s JS-vs-Python parity tests), all gated by
    `importorskip`, so a machine without Playwright silently runs none of them.
  * `docs/qa/reporter_checklist.md` is 205 lines and ~29 manual items, and
    states its own scope as "the remaining ~20% that requires human judgment —
    visual quality, drag-based plot interactions, and UX feel".
  * There is **no visual-regression testing at all**: no reference screenshots,
    no image diffing, nothing that fails when the report renders wrongly but
    still holds the right state.
  Audit questions, in the order they matter:
  1. Is the checklist stale? It says drag-based plot interactions stay manual,
     yet the interactive tests carry ~75 shift/drag/mouse references, including
     `shiftKey`, `mouse.move`, and named corner/edge-drag tests. Either the
     automation caught up and the checklist was never updated, or those hits
     are incidental. Settle that first — the answer changes how big this job
     is.
  2. Which of the ~29 manual items are automatable as state assertions rather
     than as pictures? Every one that is, should be.
  3. What genuinely needs a picture? That is the case for visual-regression
     baselines, which need the capture script logged above as their
     precondition (deterministic zoom, crop and `deviceScaleFactor`, or the
     diffs will be noise).
  4. Per-metric editor parity: `tube`, `points`, `range`, `event-timing`,
     `dominant-frequency` and `scalar` all appear in the interactive tests, but
     appearing is not covering — for each, is the editor *exercised*, or only
     rendered? The `scalar` leaf is the known-bad case (see the UI-vs-code
     parity idea: its `quantity` field renders as a raw-JSON textarea), which
     is a hint that "named in a test" and "verified usable" have diverged.
  5. Does the gating hide failures? `importorskip` means a CI or dev machine
     without browsers reports a green suite. Whether Playwright should be a
     hard requirement of the dev extra is an owner call.
  Why it matters: the headline documented claim is that bands, checkpoints
  and FFT peak boxes are *dragged directly on the plot*. If drag is the least
  automated part of the surface, the most-quoted UI claim rests on the
  thinnest test coverage — which is the reason this audit is worth doing before
  the public 1.0 push rather than after.

- **Review whether `.id_high_water.json` is necessary, or derivable from what
  is already on disk** (2026-09-03, owner: "it smells like a workaround for
  something"): the file exists (D99) because the ID index is built by scanning
  — design principle 3 — so a ref file IS the only durable record that its ID
  was ever allocated. `cleanup_obsolete` (`storage/reference_store.py:445`)
  `unlink()`s obsolete ref files, and that unlink is the single event that
  destroys the record; the dot-file exists to remember what the unlink forgot.
  It is the only ref-file deleter in the codebase, and `manifest cleanup` is
  dry-run by default, so the whole hazard flows through one narrow path.
  The owner's instinct is worth testing against that: **make the deletion
  non-destructive of the ID instead of adding a second store.** Candidate —
  have `cleanup_obsolete` rewrite the ref file in place as a stub (model_id,
  `status: "obsolete"`, no trajectory payload) rather than unlinking it.
  Cleanup's real job is reclaiming trajectory bulk, not directory entries, and
  a scan then yields the true high-water forever.
  What that would buy, beyond deleting one file kind: the whole race
  mitigation in `_bump_high_water` (`storage/ref_index.py:167`) goes with it —
  a bump currently folds in a FRESH disk scan because a read-check-write mark
  is last-writer-wins and can *regress* under two racing `--accept` processes
  (an adversarial-review finding in D99, and write-then-verify does not close
  it). No second store, no race to mitigate.
  Also worth noticing while in there: `cleanup_obsolete` still deletes each
  deleted ref's `soft_checks/` and `companions/` dirs justified by "a later
  reuse of this ID must not inherit another model's role data" (review
  2026-07-06, finding 32) — i.e. the code carries *two* mitigations for ID
  reuse, one assuming reuse happens and one preventing it. Whichever design
  wins, that comment should stop contradicting it.
  Arguments for leaving it alone, to weigh honestly: it is two lines of JSON,
  reviewed and tested; absent or corrupt it degrades to scan-only, so it never
  fails an accept; and neither design survives a user's manual `rm` of a ref
  file. Check before changing: what else assumes a cleaned-up ref is gone from
  disk (`list_obsolete`'s contract, the manifest CLI's reporting,
  `tests/test_storage.py`'s high-water tests), and whether a stub file would
  confuse `RefIndex` scanning or any downstream reader of the partition.

- **A merged cross-backend report** (2026-09-03, owner, from a review of how
  simulators are selected): today one `dstf run` is one backend, and
  everything downstream is partitioned by it — references at
  `<reference_root>/<Backend>/<os>/`, work dir defaulting to
  `testing_output/<library>/<Backend>/<os>/` (`config.py`), and therefore
  `dashboard.html`, `reports/` and the result sidecars too. That partitioning
  is why no fan-out layer was ever needed: two invocations, even concurrent
  ones, never collide, so the shell or a CI job matrix *is* the fan-out layer
  (verified 2026-09-03; `--backend` takes one name and there is no
  matrix/all-backends flag anywhere in the CLI).
  What the partitioning does not give is a single view. Running a library
  against Dymola and OpenModelica yields two dashboards and two exit codes,
  with no page showing the suite across backends and no one place that says
  "these four tests pass on Dymola and fail on OpenModelica". That view is
  what this idea is.
  Design constraints, so the work starts from the real ones:
  * **Join on the model ID, never on `ref_NNNN`.** Numeric IDs are allocated
    per partition (`RefIndex` is per `ref_dir`, and so is the high-water
    mark), so `ref_0007` under `Dymola/linux` and under `OpenModelica/linux`
    are unrelated. The model ID stored inside each ref file is the only
    cross-backend identity.
  * Not the same thing as cross-backend *scoring*, which already exists: a
    `soft_check` imports another backend's primary so cross-simulator
    disagreement is scored inside one run (the demo's `MetricTreeTest`). This
    idea is about display and roll-up across independent runs, not about
    making one run's verdict depend on another's.
  * `--merge` is not it either: it widens a filtered rerun's report to the
    whole batch manifest within one work dir and one backend
    (`cli/main.py:233`).
  * Expect ragged matrices: a test may be absent, `no-baseline`, or
    `sim-failed` on one backend and pass on another; the OS axis is real too
    (`<Backend>/<os>`), so the natural shape is model x (backend, os), not
    model x backend.
  **Owner's steer, 2026-09-03: yes to the roll-up exit status — the goal is
  maximum value for both CI and the interactive user.** So the merged page is
  not display-only: it carries its own verdict, giving CI a single required
  check across the matrix, which a CI matrix cannot express on its own. Still
  open: a new CLI verb reading N work dirs (`dstf summarize <dir>...`) versus
  teaching the existing dashboard to accept several.
  Process, per the owner: this idea gets grilled, researched and reviewed
  before any plan or execution — do not treat the entry above as a spec.

### Publicly stated future directions

These three have been stated publicly, which changes their status: they are no
longer private "somedays" but directions others have been told to expect.
Recorded 2026-09-03 at the owner's request. Each still gets grilled before
planning.

- **UQ: promote the reserved Dataset kinds and score ensembles** (direction
  1; answers a standing request for UQ and sensitivity analysis):
  `DatasetType` already reserves `EVENTS`, `SPECTRUM`
  and `DISTRIBUTION` (`simulators/types.py:46`) while every backend emits
  `TIME_SERIES` and `SCALARS` is scorable today. Giving the reserved kinds the
  same standing as trajectories would let `event-timing` and
  `dominant-frequency` share machinery with metrics added later, and the
  distribution kind is the seam UQ enters through. An ensemble run — one model
  swept over uncertain parameters — returns a sample per output instead of a
  single trajectory, and scoring that needs metric shapes DSTF does not have:
  a quantile band doing for an ensemble what the tube does for one run, and a
  Kolmogorov-Smirnov statistic comparing a whole sample against a stored one.
  The stated claim, which the work must keep true, is that each of these is a
  new plug-in rather than a change to the framework. Second half of the
  commitment: align the stored representation with the common UQ description
  proposed by Bajand et al. (2025) so a baseline
  carries the same uncertainty description a Modelica, FMI or SSP toolchain
  already produces. Relates to the E-tier "Phase 9 dataset types" note above,
  which lists #23 Frechet, #24 spectral coherence, #26 ISO 18571 and #25
  x-tolerance as the other leaves those kinds unlock.

- **Sensitivity analysis as a reader of stored scores** (direction 1, second
  half): the stated claim is that every leaf's score is stored with the
  trajectories it scored, which is the input a ranking tool needs. Nothing is
  required of DSTF here beyond keeping that true and being able to hand the
  records over — per D66 the ranking tool itself is downstream, not ours. Worth
  a check that the per-leaf score records really are complete and reachable for
  an external consumer (the JSON outputs, not just the HTML), since that is
  now asserted publicly.

- **Make cross-backend cross-checking routine** (direction 2): the
  cross-backend chain exports a model as an FMU from one backend and replays it
  on another, so one backend's regression becomes a cross-check on another's.
  Making that automatic — every backend pair compared without an author
  asking for it — is the stated next step from the capability
  declarations. Today the chain is EXPERIMENTAL and Dymola-only for export;
  idea #54 above (OM FMU export via `buildModelFMU`) is the prerequisite for
  even one more pair. Note the design question this raises: "every backend
  pair" implies a fan-out the CLI does not have, which is the same seam as the
  merged cross-backend report idea logged above — the two should be grilled
  together.

- **Tools that propose specification changes from evidence, wired into each
  ecosystem's test runner** (direction 3): the proposing half is already
  logged as the **Phase 7 rule-based recommender** (B-tier above; see D66) —
  now stated publicly, with the mechanism given as (a recommender emits a patch and validation rejects a bad proposal
  before it reaches the repository). The half not previously recorded is the
  integration: making those proposals reachable from each ecosystem's own test
  runner, which is where DSTF meets existing CI setups. Also related to the
  merged cross-backend report's roll-up exit status, which is the other
  CI-facing gap.

- **~~Write baseline JSON with LF on every platform~~** — **DONE** (D122,
  2026-09-03). Fixed at the default rather than the call sites:
  `_atomic_write_text`'s `newline` is now `"\n"`, which covers all ten of
  its JSON callers; `config.py`, `cli/common.py`, `cli/spec_cmds.py` and
  `report_suite.py`'s byte-stable `interactive.js` took the explicit
  kwarg. Wider than logged below: `spec_parser`/`patch_apply` write
  `test_spec.json`, also a promised surface. No migration — existing CRLF
  files read back fine and flip on their next accept. An accept on
  Windows is still unrun. The `.gitattributes` half of the original
  entry below is NOT done — suggesting `*.json text eol=lf` to consuming
  repos is a docs question left open. Original entry, kept for its
  call-site list:

  > The defect (2026-09-03, from the
  TRANSFORM tree showing two baselines as wholly modified when nothing had
  changed): `_atomic_write_text`
  (`storage/ref_index.py:30`) forwards `newline` to `open`, and every JSON
  caller — `reference_store.py:301`, `:358`, `:385`, `roles.py:235`, `:382`,
  `:441`, `batch_manifest.py:69`, `ref_index.py:198` — takes the default
  `None`, so Python translates `\n` to `os.linesep`. A baseline accepted on
  Windows lands CRLF; the same file accepted from WSL lands LF. The CSV
  export at `reference_store.py:426` already passes `newline=""` on purpose,
  so the mechanism is understood in this file, just not applied to JSON.
  Why it matters beyond tidiness: the reference tree is a surface other
  people version-control (the stage block names it), and a cross-platform
  accept turns a one-value edit into a whole-file diff — 6,881 changed lines
  on the observed `ref_0011.json`, with the real change invisible inside it.
  Fix: pass `newline="\n"` on the JSON writes. Small and contained.
  Old data, not old code: files already stored with CRLF still read fine, so
  no migration is needed — but consider whether `manifest`-style tooling
  should offer a one-shot normalise, and whether the docs should suggest
  `*.json text eol=lf` in the consuming repo's `.gitattributes` as a belt
  alongside it. Verify on both platforms before claiming it fixed.

- **Two leftovers from the D122 review, both small** (2026-09-03, from the
  adversarial review of the line-ending fix): (1) `ref_index.py`'s ID-claim
  stub writes its JSON with `json.dump` and no trailing newline, so a crash
  between `register()` and `store_reference()` leaves a ref file that git
  flags `\ No newline at end of file` for as long as it survives. Pre-dates
  D122 and is orthogonal to line endings; one `+ "\n"` if it is worth
  touching at all. (2) JUnit XML is the one text output DSTF writes that
  still takes the platform newline — `ElementTree.write`'s filename branch
  opens without `newline=`, so fixing it means passing a file object rather
  than a kwarg. Excluded from D122 deliberately (CI artifact, regenerated
  wholesale, content differs every run); only worth revisiting if someone
  reports committing `results.xml`.

- **`import-baseline` stores whatever path the user typed, absolute included**
  (2026-09-03, found during a pre-publication scrub of this repo):
  `cli/baseline_cmds.py:216` does `provenance.setdefault("imported_from",
  str(source))`, so importing a soft_check by absolute path bakes that
  absolute path into a JSON file the user then commits. This repo shipped one:
  the demo's `openmodelica.json` carried a full absolute path from the
  machine that produced it, until it was rewritten by hand. The reference tree is a surface other people version-control, so
  the leak is theirs, not ours — a username and a directory layout published
  by a tool they ran. Likely shape: record the path relative to
  `reference_root` (or the source repo root) when the source lies under it,
  and only fall back to the literal string when it does not. Needs grilling
  first — "relative to what" is the actual question, and provenance that
  cannot be resolved later is worse than none.

- **Raise the timeout on `SteamWater_HCSG`, or the default** (2026-09-04, from
  the pre/post-rename verification runs against the external TRANSFORM
  library): that test is the only one of 331 that does not pass, and it fails
  as `SimulationError: Timed out after 60s` — a slow model against the default
  budget, not a wrong answer. It reproduced identically before and after the
  Backend rename, so it is unrelated to that work, but a timeout-flaky test in
  the flagship external library is a poor first impression for a public 1.0.
  Fix is one field: `simulation.timeout` for that entry in its test_spec.json,
  or a higher global `--timeout`. Worth deciding which, since the same
  question applies to any long-running model a new user brings.

- **~~Three sources decide which ruff runs, and only one of them is the lock~~**
  (2026-09-04) — **DONE**. `uv.lock` and `.pre-commit-config.yaml` both pin
  0.16.6; `pyproject.toml` carries a floor of `>=0.16`, which still permits
  0.16.0-0.16.5, so the two pins agree and the floor merely rules out the
  pre-0.16 behaviour the false alarm turned on. Markdown is excluded from
  formatting. `quality.yml` passes `--locked` so CI asserts the lock rather than
  silently re-locking on drift; the documented developer setup is `uv sync
  --extra dev --locked` in every doc; and the runtime hints for a missing extra
  (an ImportError for FMPy, a RuntimeError for OMPython, a printed line in
  `check-openmodelica`) give the additive `--extra <x> --locked --inexact` form
  rather than one that prunes.
  Worth keeping from the investigation, because two reviews had to correct it:
  `uv sync` prunes anything you did not name **on that command line** — being
  present in the lock does not save it — so extras must all be named at once,
  and packages outside the lock entirely (`pytest-playwright`, `pre-commit`)
  need `--inexact` or a standalone `uv tool install`. What remains unpinned is
  the interpreter: the same lock resolves numpy 2.2/scipy 1.15 on 3.10 and
  numpy 2.4/scipy 1.17 on 3.12, and neither the repo (no `.python-version`) nor
  `quality.yml` pins one, so "the same lock as CI" is true while "the same
  versions as CI" is not. A `.python-version` would close that; it also freezes
  the dev interpreter, which is why it was not taken silently.

- **Publish to PyPI as `dynamic-systems-testing-framework`** (2026-09-04; the
  NAME is settled, the publishing is not): `dstf` is taken there by an unrelated
  2020 project, so `README.md`'s `uv tool install dstf` fetched someone else's
  package. The rename landed (D124) — no behaviour changed, only `[project]
  name` plus the two `importlib.metadata` lookups that read it back and the
  install lines in the docs. What is still open is the publish itself:
  `https://pypi.org/pypi/dynamic-systems-testing-framework/json` returns 404
  today, so the README's install instruction is correct in form and does not yet
  work for a reader. Two things to do when publishing: the metadata is thin
  (`[project]` has no `readme`, `authors`, `urls` or `classifiers`, so the PyPI
  page would render an empty body), and anyone who tool-installed the package
  while it was named `dstf` should `uv tool uninstall dstf` first, since an
  upgrade under the old name now resolves the squatter.

- **Guard the whole class, not the two known call sites: a source scan for
  metadata-name literals** (2026-09-04, raised by the review of D124 and not
  taken): `tests/test_distribution_name.py` pins `dstf._DIST_NAME` to
  `pyproject.toml` and proves both current `importlib.metadata.version()` call
  sites resolve. It cannot see a THIRD one — a future
  `version("dstf")` literal added anywhere in `src/dstf` is invisible to it, and
  that call fails silently by design (the fallback string, not an exception).
  Shape: an `ast` walk over `src/dstf/**/*.py` asserting every `version(...)` /
  `metadata.version(...)` argument is the `_DIST_NAME` name node, never a string
  literal. Small, and it converts a pinned instance into a pinned rule. Worth
  weighing against the cost of a test that reads the source tree — those go
  stale in their own way when the import style changes.

- **~~Finish the role vocabulary inside `tests/`~~** (2026-09-04) — **DONE**.
  The residual was 326 `runner` occurrences (not the ~277 logged), plus four
  that the D123 sweep had left in `src/`: two `Worker`-subclass docstrings
  calling the class "a test runner", and a `_runner()` thread closure in
  `backends/persistent.py`. The mapping turned out to have no judgment in it —
  every `*Runner` class in `tests/` subclassed `Backend`,
  `PersistentBackendBase` or `JsonDriverBackend` and NONE subclassed `Worker`,
  so `runner` → `backend` applied uniformly.
  What the sweep must NOT touch, and why a future grep still returns hits:
  `tests/test_scorer_parity.py` writes three temp node scripts named
  `parity_runner.js` / `state_runner.js` / `trust_runner.js`. That is a
  different and correct sense of the word — a script that runs, not a DSTF
  backend — so those literals and the comments referencing them stay. The
  invariant to check is "every surviving `runner` is a `*_runner.js`
  reference", not a raw count.
  Two prose traps found by review, both worth knowing before the next sweep:
  a "test runner" in `test_python_backend.py` meant *pytest*, and became a
  false "test backend" until it was corrected to "the pytest process"; and
  renaming the JS-harness comments to "harness" severed a pointer to the
  `*_runner.js` filenames it was describing. A vocabulary rename is mechanical
  in code and emphatically not in prose.

- **Cancel a running job** (2026-09-08): no cancel path exists in
  `ProgressReporter`, `run_tests` or the persistent worker pool. Backend work:
  a cancel token threaded through `run_tests` and honoured by the pool's
  dispatch loop. The authoring tool's job panel will want it eventually;
  deliberately not promised by its v1 (two-tier spec, §9).

- **After the cut (2026-09-08), small follow-ups** — the two-tier split's
  sub-projects 1 and 2 shipped (D125); these came out of their reviews and
  none blocks sub-project 3:
  - The dashboard's command menu should offer
    `dstf author --config <cfg> --filter <selection>` (spec §4.4) once the
    command exists — sub-project 4. The builder (`dashboard.js commandKind`)
    takes a kind; it is one more option.
  - A leaf's authored parameters (tube widths and interpolation, points
    `time_tolerance`, event-timing `count_must_match`, scalar
    `tolerance_mode`) were readable on the page as pre-filled inputs and are
    not now; `summarize().details` covers some. If the read-only page should
    show them, it is a per-mode `details` addition — decide when the
    authoring tool renders the same tree macro.
  - A per-variable tree mount prints the child count of what it shows
    (`and [2]` under `x`, `[7]` in the full tree); the deleted JS printed the
    unfiltered count. Cosmetic.
  - This file's older entries still describe the browser editors and the
    parity test in the present tense (the reporter-as-IDE vocabulary); they
    are history now and the entry that proposed the cut is done. A
    docs-only sweep, together with `docs/CODE_REVIEW_2026-07-06.md`'s two
    mentions if that dated review is ever edited.
  - `figures/` is outside mypy's scoped file list and type-checks clean;
    one line in `pyproject.toml` adds it.

- **After the Session (2026-09-09), follow-ups** — sub-project 3 of the
  two-tier split shipped (the Session, the capability contract, the three
  seams; D126 for the tree-scores-what-it-names rule). None blocks the shell:
  - 70 TRANSFORM tests cannot have their criteria edited in the tool: their
    recorded result variable names carry a newline (a line-wrapped `x=`
    expression the `.mo` parser never normalised, e.g.
    `heatTransfer.alphas[\n        1, 1]`); the flat path labels them `x[N]`
    for display and no tree leaf can name them. The fix is whitespace
    normalisation in the parser's `x_expressions` and the result naming,
    which also changes the names stored in baselines — a coordinated change.
  - `read_results` prints its progress to stdout; the Session inherits it on
    open and on every reload, inside a UI process. A `quiet` flag or a logger
    belongs with the run callable (sub-project 5).
  - `Session.open` is all-or-nothing on a comparator exception: one malformed
    flat override (`tube_width_mode: "bogus"`, `tolerance: "abc"`) raises out
    of `compare_one` and no project opens. Pre-existing in `compare_all`; the
    tool needs per-test isolation.
  - `JobContext.project` hands a job the mutable project (store, backend,
    results); the read-only view's `_store` and `_session` are reachable by
    name. Enforcement is Python-convention strength; the design chose that
    line.
    CLOSED by D150 for its first half: `JobContext` and the `Job` outcome
    are deleted — a capability starts no run of its own.
  - `spec-update` cannot materialize a tree for a pattern-tracked or
    annotation-tracked test (it has no result to resolve against); the tool
    can. Either accept that the CLI is the narrower editor or give it a
    `--from-results` that reads the last run.
  - The review loop's `[a]` still calls `store_reference` directly; route it
    through `accept_run_results` when the tool's Accept lands.
  - `docs/patterns.md` still describes the pre-D125 browser editors in the
    present tense; a docs sweep with this file's older entries.

- **After the shell (2026-09-14), follow-ups** — sub-project 4 of the
  two-tier split shipped (`dstf author`, Edit criteria, Edit tree, the form
  emitter, the conformance test). None blocks sub-project 5:
  - Edit tree offers wrap, unwrap, add, remove and combinator kind / k /
    weights / threshold. The deleted browser editor also offered the
    `weighted` `direction` and a "reset leaf to original"; neither has a
    control. `direction` keeps whatever the node has.
  - The tabs region is rebuilt on every selection and job event; a control
    being typed in survives a stage (tabs are not rebuilt on spec/results),
    but a job event mid-keystroke would drop focus. Only the Chromium smoke
    test types into a real field.
    CLOSED by D150 where the job is concerned: there is no `job` event.
  - An `int` form field rounds on blur in the browser (`precision=0`) and
    truncates on the server; no registered mode has an int field today.
  - Two browser tabs on one shell share the drawer element: the first tab's
    pending button toggles the second tab's drawer. One process, one tab is
    the v1 shape (§4.7); a per-client drawer is the fix if that changes.
  - A cleared `str` form field stages `""` rather than omitting the key.
  - `Session.open` still prints the backend's read-phase progress to stdout
    inside the UI process (from the Session follow-ups; plan 5's run
    callable is where a `quiet` flag belongs).
  - The conformance test reads `git status` once per module; on this /mnt/d
    tree that call costs 1.5 s, and a concurrent editor's write during the
    module trips it with a message naming the moved lines.

- **After accept and rerun (2026-09-14), follow-ups** — sub-project 5 of the
  two-tier split shipped (`pipeline.run_pipeline`, `select_backend`,
  `Backend.quiet`, Accept, Rerun, the job panel on `status.json`, the §6
  end-to-end test over the Python example). None blocks sub-project 6.
  (Rerun and its job became Queue and the simulation queue in D150; the
  items that closed are marked.)
  - A partial rerun (the tool's, or `run --filter X` then `compare`) can flip a
    NON-rerun test's verdict on OLD data: a manifest entry with no
    `last_run_status` whose only failure record was the previous `status.json`
    row loses that row when the subset snapshot overwrites the file, and a
    parseable result reads back as a success. Current-writer data is immune
    (`mark_pending` / `record_result` touch only in-scope entries). The fix
    belongs in `ProgressReporter` / `_apply_status_verdicts` (carry the
    out-of-scope rows forward), not in the tool.
  - `status.json` is per invocation, so a partial rerun's dashboard shows the
    rerun subset, exactly as `--merge` leaves it.
  - `Backend.quiet` gates every print of ours; Dymola's own interface logger
    (`dymola/persistent_backend.py` `_filtered_print`) still writes to the
    tool's console — all but the connection errors of a Dymola a worker
    closed — and Python's last-resort logging handler prints WARNING+
    (nothing configures logging in `src/`).
  - `PersistentBackendBase.run_tests` closes its workers on the normal path,
    not in a `finally` around the dispatch loop; in a long-lived tool process a
    job that raises there could leak a Dymola/OMC worker per Rerun. Unverified
    (the named-as-unrun owner gate is where it would show).
    CLOSED by D150 for the tool: its queue runs on a pool the Session closes
    (`stop_queue`), not through `run_tests`. The CLI's `run_tests` is as
    described, in a process that ends with the run.
  - `overlay_loader._sibling_backend_index` is `lru_cache`d for the CLI's
    lifetime; a tool process that accepts into a sibling partition and then
    re-renders serves stale cross-backend overlays.
  - Accept's forecast cannot see a non-finite trajectory: the row reads `will
    store`, the shared function declines it, the notice line says so. A
    finiteness scan over the result in hand would close it at the cost of
    touching every sample on every tab refresh.
  - `Accept.available` reads the batch manifest through `BatchManifest.load`,
    which moves a corrupt file to `.corrupt` — a write side effect in a read
    path, reachable only if the file corrupts between open and a refresh.
  - `run -i` still offers `[a]` for a simulate-only test (it now stores
    nothing and says so); `--accept` and `[a]` print a poisoned trajectory's
    refusal twice (the store's `logger.warning` plus the loop's line).
  - The unregistered-backend message reads `Available: (none)` when no builtin
    backend has been imported yet (`get_backend_class` imports only the
    requested name).
  - Two jobs cannot queue and there is no cancel (§9); a second Rerun while
    one runs is refused with the running job's label.
    CLOSED by D150: the queue, with Remove and Cancel.
  - `Session.open` builds its reading backend with `persistent=False`; the
    queue's pool builds its own persistent-preferring one. Two backend
    instances per project is the v1 shape.

- **After on-plot editing (2026-09-14), follow-ups** — sub-project 6 of the
  two-tier split shipped (the editable figure, `edit_handle` on the
  descriptor, `columns` on the Config list fields, the hook's per-shape
  payload contract, the in-place centre figure, panels that follow the spec,
  Edit on plot). None blocks v1:
  - Tolerance boxes and windows (`points/box`, `peaks/window`,
    `events/window`) are drawn as draggable shapes but refused: resizing a
    tolerance on the plot is the natural next handle.
  - One process, one browser tab: the in-place redraw and the figure hook
    follow the last-built plot, so a second tab's centre does not redraw in
    place and a drag there resolves shape indices against the other tab's
    figure.
  - The hook resolves a shape index against the figure the server last sent;
    a drag released between an `update_figure` that changed the shape list
    and the browser applying it can name the wrong shape, and that edit
    stays staged (the snap-back only corrects the picture).
  - Overlapping shapes steal each other's drags — the later shape in
    `layout.shapes` wins: two rings within ~14 px, or a `scalar/max` line at
    a `range/max` line's value. Nothing tells the user which one moved.
  - Every edit rebuilds the other capabilities' panels: Edit on plot's tables
    measured 13 ms (6 leaves × 2 rows), 212 ms (20 × 10), 2.4 s (60 × 30) per
    rebuild, and Accept reads the batch manifest each time. A job that
    finishes while a field in another capability is being typed in rebuilds
    it.
  - The pixel-scale no-move tolerance uses the full reference span, not the
    leaf's window, so deep zoom into a narrow window brings it closer to a
    pixel; `Add row` midpoints are not rounded to 12 significant digits as
    drag writes are; a non-numeric value in a number column of a table is
    not refused.
  - After Edit tree removes a child, a stale drag id could resolve to the
    leaf that shifted into its path when that leaf has the same metric (a
    wrap is refused safely).
  - A structural Edit tree action leaves the selection's leaf path naming
    what is now a combinator; Edit criteria then says the leaf is not in the
    scored tree rather than following the leaf.
  - The report vendors plotly.js 2.35.0 while NiceGUI bundles 3.8.2; only the
    tool's figure is editable, so nothing depends on the drift yet.
  - The committed browser test compares both axis ranges after an x-only
    zoom; that holds only because its band edit does not grow the data's y
    extent (an x-only zoom leaves y on autorange).
  - `docs/extensibility.md` still describes the interactive report's live
    re-scoring and add-leaf dropdown, and `docs/patterns.md` future editing
    through JS templates — both predate the split.
- **After the foundations corrections plan (2026-09-16), follow-ups** — noted
  while specifying what the Session does when one model's scoring raises
  during a reload (the plan leaves existing behaviour in place):
  - `Session.open` propagates a scoring failure from `_reload_results` with
    the project set and scored, but `loading(phase="done")` never fires and
    `rescan_done` is left as it was; `dstf author` then fails to start. A
    `finally` that emits `done` and sets `rescan_done` would make the Session
    usable after a partial scoring failure — sub-project 2's shell decides
    whether it wants that or the hard stop.
  - No test drives a `Plotly.react` in the tool's page (NiceGUI's plotly.js
    3.1.1); the axis follow after a react is exercised by the report-tier
    emulation and a hand probe in the gate only. A smoke test that stages an
    edit after a legend click and re-probes `yaxis4` would close it.
- **After the foundations corrections build (2026-09-16), follow-ups** — from
  the seven fresh-context reviews; none blocks sub-project 2:
  - `_reload_results`'s raise counts `failures` per `_score` call, so a model
    under two manifest keys (hand-edited manifests only; the tool never writes
    one) can read "2 of 1 models"; a dict keyed by model would fix the message.
  - A model whose scoring raised is announced by the same `loading(results)`
    event as a scored one; a per-event row filler reading `comparisons[model]`
    gets a KeyError. An `error=True` payload key when sub-project 2 consumes
    the events.
  - A leaf-row click in the current shell REBUILDS the plot element (two
    `newPlot`s observed for a same-key selection) although `_follow_centre`'s
    docstring says a same-key selection keeps it; pre-existing, zoom is lost.
    Sub-project 2 replaces the shell; watch it there.
  - `store_scan` leaves `<key>.<pid>.<tid>.tmp` behind when the rename fails
    after the write; harmless to `load_scan`, litter in the user's cache dir.
  - `_rebuild_tests` drops a gone model's comparison and read but not its
    `project.results` entry; every consumer keys off `tests`, so it is
    unreachable today.
- **After the Suite page and shell build (2026-09-16), follow-ups** — from
  the sub-project 2 reviews (one per task, plus one per correction); none
  blocks sub-project 3:
  - `LoadState.failed_models` is a snapshot of the OPEN and is never cleared:
    a model whose RESCORE raises after an Accept shows `…` (queued), not
    "scoring failed", and one that failed at open and was fixed by a rerun is
    masked by `comparisons` being read first. Per-model scoring errors
    belong in the Session (or keyed per `results` event), not in the shell's
    load state.
  - A Job or StoreWrite applied WHILE an open is in progress is not refused
    by the Session (`_refuse_if_busy` knows jobs and pending edits only), so
    its `loading(results)` events would be absorbed as the open's and its
    `_reload_results` would run beside the open thread's. No shell caller
    exists yet; the queue (sub-project 5) is where a rule belongs.
    (The queue is built — D150 — and `Job` went with it.)
  - Every Session event refreshes the whole header including the pending
    drawer (a clear and rebuild of every staged op): N `results` events in
    an open are N drawer rebuilds. Coalesce to one refresh per tick.
  - Check-all is N checkbox `set_value`s, each firing `_toggle` →
    `refresh()` (O(N) each): O(N²) over 331 rows. Not measured in the gate.
  - `ActionBar.errors[cap.name]` persists after Close: the next open of the
    same capability shows the stale error until an act succeeds.
  - `app.py`'s `port or 8080` turns `--port 0` (ephemeral) into 8080;
    `terminate_children` neither waits nor prunes (zombies until
    `app.shutdown()`); `_free_port` releases the port before the child binds.
  - The conftest module-restore fixture leaves a `dstf.*` module FIRST
    imported after a simulation's pop within the same test attached to a
    duplicate parent; no test does it today.
  - Stale prose from the tab strip: `edit_tree.py`'s docstring names
    `Shell.act` (no such class) and "the tab"; the same word at
    `edit_tree.py` ~95/97, `capabilities.py` (`Panel.submit`) and
    `tests/test_capabilities.py`; two ported test docstrings in
    `tests/test_shell.py` name `Session(post=shell.post)` and `_jobs`.
    Sub-project 3 or 4's sweep.
  - The disabled Save draws as a filled primary-blue button beside "no
    pending edits" (the gate's header crop) — the most prominent element in
    the header while nothing is pending. Owner's call, with the picture.
  - `Shell._reported` (a raising `available`/`caption` logged once per
    capability and method) is never cleared: after a reopen on another
    backend a DIFFERENT exception from the same method is not logged (the
    why-line still shows it). "Once per open" — clearing it in
    `open_async` — is the alternative; owner's call.
- **Post-redesign: every backend end to end, with the complex criteria
  (2026-09-16, owner)** — to be fleshed out once the redesign's seven
  sub-projects are complete. What the inventory found: CI runs only the
  Python tier and the mocked unit tests (every real-tool marker is
  deselected); OpenModelica (40 tests over a real `omc`) and FMPy (14 over
  the Reference FMUs) run for real on the owner's machine; Julia's three
  end-to-end tests over `JuliaMtkTestingLib` have not run in any recorded
  session — Julia is not on PATH here and the example project is not
  instantiated; Dymola has NO end-to-end test at all (the `dymola` marker is
  declared and unused; the `dymola` Python package is not in the venv) — it
  is exercised only by the hand-run TRANSFORM gates, and only their rescoring
  path since 2026-09-08. The Modelica and Julia example specs carry every
  mode (tube, points, range, scalar, event-timing, dominant-frequency) and
  every combinator (and / or / k-of-n / warn / weighted, `against`,
  `window`), and nothing simulates them automatically: the metric tree is
  proven on synthetic arrays only. The task: install Julia and instantiate
  the example project so its tests run; add a marked, skip-if-absent Dymola
  end-to-end test over `ModelicaTestingLib` (the Julia one is the model);
  run the example libraries on every installed backend and score their
  complex criteria against real results; decide what of this a CI runner
  can carry. The OpenModelica rerun hang (sub-project 5's acceptance, still
  unrun) belongs in the same pass.
- **Follow-ups from the `plots` key / plot figure build reviews (2026-09-20)**
  — residuals the reviews found and the build did not take on:
  - For the Test page plan: `Session.plot_figure` of SEVERAL series
    re-assembles on every call — one `build_trajectories` (every scored
    variable to lists) and one decimation per series, no disk — while the
    single-series path serves a cached decimated base. The page's in-place
    redraw per plot wants `figure`'s base-plus-leaves view extracted into a
    helper and `plot_figure(views, None, editable=True)` over cached bases.
    Leaf geometry on a multi-series plot carries no series attribution
    ("Tube" appears once per series, in the fixed actual / reference colours,
    outside the family's legend group). `Session._test_dir` re-reads
    `batch_manifest.json` per call (one load per cold `plots()`).
  - For the drawers plan: a layout staged on a test that is tracked only by
    its `.mo` annotation creates that test's FIRST `test_spec.json` entry
    (`model` + `plots`, no `variables`); it scored the same in the TRANSFORM
    gate, but the drawer should say it is creating an entry. (TRANSFORM's
    newlines inside brackets, `massPorts[\n        4, 1].C[1]`, are gone:
    D132 names the variable `unitTests.x[i]` and `normalize_expression`
    collapses the expression's whitespace — 0 newlines in any baseline or
    the spec on 2026-09-25.) `family_of` refuses a name with a comma index anywhere
    (`x[1,2].y[3]` has a single last index and does not fold); `[007]` parses
    to 7. `discovery/scan_cache._to_json` guards `metric_tree_spec` but not
    `plots` (unreachable today: a scanned test has no layout).
  - The family's legend swatch is one line in the ramp's lightest stop;
    DESIGN.md once asked for "a small ramp" (the sentence was dropped with
    D130). A plot whose every series is missing renders an empty heading.
    A no-baseline plot of a tracked and a plot-only series prints the "plot
    only" line for the second and says nothing of the first.
  - Process: a plan task that was only READ before the build needed the most
    repair (the report page: a guard order that broke an existing test, then
    twelve review fixes); the four tasks a reviewer EXECUTED in an archive
    first needed almost none.
- **Follow-ups from the Test page build and its gate (2026-09-21)** — residuals
  the reviews and the TRANSFORM gate found and the build did not take on:
  - For the owner, each with a picture from the gate: the wrap / add / edit
    forms open as popovers from their icons (never drawn on a board; an open
    one covers the neighbouring rows' icons and shows k / weights / threshold
    for every combinator kind at once); the plot legend sits inside the plot
    and can cover a criterion's rings near the start of a trace; the tool's
    plots no longer zoom on the wheel (the page scrolls; the report still
    zooms); the report draws a section for a tracked variable no criterion
    scores.
  - A test whose variables are relabelled `x[N]` (a name starting `cat(` or
    holding a newline; about 70 in TRANSFORM) has no editable tree: `Session.
    tree` will not materialize an implicit tree whose names differ from what
    was scored, so every criterion there is read-only. Mapping a label back
    to its raw name by index in `_materialization` is the likely fix.
    CLOSED by D132 (measured 2026-09-25 over a copy of TRANSFORM at
    6d9bb52): 330 of 331 trees editable, the one without a tree is the
    SIM_FAIL timeout; the 147 tests with `cat(`/multi-line expressions all
    edit, since every scored name is now `unitTests.x[i]`.
  - Layout at 1280 px (2026-09-14, before the Suite and Test pages; Rerun
    is Queue since D150): the shared header's first row wraps to two lines and
    the sub-header's Accept / Rerun stack; the tube editor (455 px) plus its
    320 px plot does not fit a 900 px screen, so the control-points table is
    below the fold; Edit criteria still shows a list field as a raw JSON box
    beside Edit on plot's table of the same rows.
  - Switching a criterion's mode passes through a misconfigured leaf (mode →
    tube before a width mode is chosen): the row reads FAIL with the config
    error until the second click. A mode switch should stage the new mode's
    defaults in one op.
  - `+` beside a criterion under a `warn` (one child only) or a `weighted`
    (weights per child) is always refused by the validator; the control could
    grey with that sentence instead.
  - A non-browser GET of `/` or `/test/…` (curl, a health check) logs a
    `tab_state()` traceback: `app.storage.tab` needs a connected client.
  - `follow()` re-reads `available` per node per repaint — one deep copy of
    the tree each: 6 ms at 40 criteria, quadratic; a spec revision counter and
    a memoized `Session.tree` when a test reaches ~150 criteria. An Edit tree
    cluster is ~28 elements per node.
  - The key-stats row of the artboards is not built: its numbers come from the
    run's statistics files, which the Session does not read.
  - Browser-only and unverified: the scroll position across a rebuild of the
    cards; "Show all" after focusing a far-down plot; a plot that failed to
    draw being re-observed.
  - Process: three rounds of correction on one line of reasoning (a tracked
    variable stays tracked → duplicate `x[N]` cards and diverging tiers → every
    no-baseline report page doubled) — each caught only by the NEXT fresh
    review, none by a test written with the fix.

- **Follow-ups from the variable-identity build (2026-09-21)** — residuals the
  eight review rounds found and the build did not take on:
  - Checkpoint markers hover as a bare number (`hoverinfo: x+y`, no label);
    overlay hovers are Plotly-truncated (`Overlay: com... : 0.15`). Pre-existing.
  - A plot-only series read straight from the result file has no expression
    carrier, so the served tool's hover test covers only label == name; giving
    the Python demo backend an expression carrier would let it cross the
    process boundary.
  - `show()` still derives the page scope per drawn card (~2 ms each, 41 at 40
    cards) because `session.plot_figure` is self-contained by contract.
  - The scan cache keys user recognizers by NAME only: a changed recognizer
    body is served stale until the digest of the discovery code changes.
  - Two instances of the UnitTests component in one model: the first wins,
    silently. Mixed `{cat(1,a,b), c}` expression lists label as
    `cat(1,a,b), c[i]`.
  - A `both` test whose spec pattern names a UnitTests expression loses that
    spec variable's baseline after migration ("re-accept needed" list); none in
    TRANSFORM.
  - The report's per-variable badge reads the plain label beside the muted
    name; the tool's multi-series pill reads the disambiguated text — two
    surfaces, one rule, but worth one sentence in usage.md if it confuses.
  - The tree-path migrate hint is logged once per (baseline, variable); the
    flat path's override warning once per compare — `run --merge` repeats it.
  - Process: a LOOK screenshot cannot show a hover; a UI rule that puts text
    into a Plotly template needs a real-mouse Playwright assertion, not a
    dict-shape test (the one REJECT of this build).
  - Companions (2026-09-22): `freeze_companion` rewrites `variables` from the
    surviving pairs, so freezing a companion whose record was entirely
    malformed turns it into an unmapped one; the tool has no overlay-status
    surface (missing / invalid / warned companions are unshown there — only
    the report's overlay table says); a CLI-added companion still stores an
    absolute path; `--map` refusal on freeze/remove exits 2 via ConfigError
    while other companion failures exit 1.

- **Follow-ups from the form row + legend build (2026-09-25)**: residuals the
  reviews and the TRANSFORM gate found and did not fix, with no owner ruling
  on them yet.
  - Swatch and error choices reset when adding or removing a criterion
    rebuilds the page (paths shift, so keeping them needs a mapping).
  - Tooltips stay up after a click. The swatch's tooltip covers the "Add a
    criterion for x" button, the icon's covers the row above, and the grey
    Wrap's covers the start of the sentence below.
  - `.dstf-form-row` and `.dstf-editor` overflow their row by 16 px; one rule
    for both.
  - At 900 px the tool's header cuts off "dashboard" and "Stop server". This
    predates the build. FIXED by 3c (D134): they moved into the header's ⋮
    menu; nothing is cut off at 900 px.
  - Plotly's "Double-click to zoom back out" notice covers the header's Export
    and dashboard buttons for a few seconds after a zoom.
  - A row taller than the viewport scrolls its title to the header's edge, so
    the criteria row that opened it goes under the header.
  - Unwrap under a weighted parent is refused with the validator's
    `…weights: length (2) must match children (3)`, which does not say
    "weighted".
  - No multi-series plot exists in TRANSFORM, so the gate could not exercise
    one. The fixture's seven-plot page is the only coverage.
- **Deferred from 3c's scoping (owner, 2026-09-25)**: two plot-layout
  controls with no asker yet, each additive later.
  - Moving a plot up or down. In 3c a new plot is placed below the selected
    one, which is the only control over order.
  - A plot title you write ("Temperatures"). It would be a new field on a
    `plots` entry in `test_spec.json`, a surface TRANSFORM-UnitTests relies
    on, though additive. Until then the toolbar names the series.
- **Follow-ups from the rail and drawers build (2026-09-25)**: residuals the
  reviews found and the build did not fix (D134).
  - Adding a criterion and then editing it stays two pending ops: the add is
    at `<parent>/children/-` (`edit_tree.py`) and the edit a replace under
    `<parent>/children/N`, so `Session.stage` sees two paths. Merging them
    would need the add staged at its index. Owner call.
  - A disabled Remove plot is out of the tab order, so the sentence saying
    why is reachable by hover only.
  - Remove plot and then Reset layout, on a test with no `plots` key, leaves
    two pending edits (`add /plots`, `remove /plots`) that together change
    nothing. The collapse keeps a remove after an add as two on purpose
    (D134).
  - At 900 px the header wraps the pending count, Save and Discard to two
    lines (owner-accepted). For the owner's hands-on look: hiding Save's
    why-sentence while nothing is pending and dropping the centre run
    indicator would likely fit the row on one line.
  - The drawer's family swatch is always the accent ramp, while a plot may
    draw that family in its second hue (a plot picks a family's hue by its
    order there).
- **Watch-only from the rail and drawers build's last reviews and its gate
  (2026-09-26)**:
  - The header menu's items claim `role="menuitem"`, but Quasar's menu has
    no arrow-key navigation; Tab moves through them. Arrow keys, or no menu
    roles.
  - A Plotly page error, "Resize must be passed a displayed plot div
    element.", fires when the rail is clicked right after a Test page loads
    (3 of 3, before and after the build). Nothing visible breaks. Likely the
    same first-second NiceGUI plotly race as the Discard entry of
    2026-09-28 below.
  - At 900 px the header's plain labels ("Python · linux", "1 running ·
    rerun") break mid-phrase; the two-line rule covers buttons only.
  - A multi-series plot's title lists its series in layout order in the
    tool; the report puts the scored variable first.
  - Over a whole result file, an expression and its own result-file name
    would fold into one group with a doubled index (`[1,1]`); the drawer
    never shows it, because it groups before folding.
- **From the feedback bug batch's review and TRANSFORM gate (2026-09-26)**:
  - A seeded `tube` FAILS on 22 of 873 TRANSFORM variables, all event or
    step signals (IntegratorWithReset, LatchHold_Test, MFBS_Test, OffTimer,
    …; worst violation 0.95 at the event t=0.2), where `nrmse` passes on the
    same pair. The cause is the tube check itself, not the seed: it compares
    the float32 actual's times against the float64 reference at duplicate
    event instants. A tube authored by hand on those signals fails the same
    way.
  - A drag stages the value where the mouse let go, unrounded
    (`610.947435897`). For the on-plot editing step: snap a dragged value to
    a precision the axis can show.
    (Built — D149: `comparison/snap.py`, a step of a thousandth of what the axis shows.)
  - On a plot with a spectrum panel, the top plot's "Time" axis title is
    drawn inside the spectrum subplot, over the spectrum near its centre.
  - `comparison/handles.py` refusals still name fields and Python reprs
    (`tube_points[0].time is None, not a number`, `tube_width_mode None has
    no draggable widths`); reachable only through hand-authored values or a
    stale figure.
- **Watch-only from the Suite table and run-timing builds (2026-09-27)**:
  - `compare --filter A` after `run --filter B` lists B as an unscored
    "pending" row beside A (B's row comes from status.json, the last run's
    scope).
  - Shutdown's 5 s wait can let a job that was only waiting on the
    background queue start as the process exits; a plug-in `after_reload`
    that calls `wait_background()` waits on itself until the 60 s bound
    (both recorded in D137 §6 as known limits).
    CLOSED by D150: the `Job` and its `after_reload` are deleted.
  - The dashboard with every column shown scrolls sideways at 1280 px; a
    grid Sim value of 1000 s or more plus its "cpu" tag can be cut at 90 px
    (the tooltip still names it).
  - `Header.refresh` reads status.json on every refresh (small file, but
    every session event).
- **Watch-only from the Test page build (2026-09-27; D139)**:
  - The pending drawer's per-edit sentences the board drew ("edit nrmse on
    pulse.y: tolerance 0.01 → 0.005") are not built (the planner's choice
    4): each section has its edit count and verdict line. A generic
    op-to-sentence renderer is its own piece of work.
  - `spec_preview.entry_lines` slows on many repeated lines (`difflib` with
    `autojunk` off): 600 leaves took 141 ms, 1,500 leaves 0.9 s. TRANSFORM
    averages 2.6 variables per test.
  - On a short screen a criterion's editor can be taller than the room
    under the header, so its top or bottom stays out of view.
  - When scoring the EDITED spec raises, the strip and the pending drawer
    read the comparison from before the edit.
  - After a queued run with edits pending (a Rerun, when this was written),
    `saved_comparison` scores the saved
    spec against the results read for the edited TestModels. Unverified:
    it matters only for a backend that reads only the named variables.
- **Watch-only from the Test page step's final review and TRANSFORM gate
  (2026-09-28)**:
  - Every Test page load logs NiceGUI's "Event listeners changed after
    initial definition. Re-rendering affected elements." once (a browser
    console warning, echoed to the server log: 27 times in the gate's log).
    It predates the Test page step: one per load at 14f1ba7 and at 7bec0fd
    alike (FakeLib, two loads each). `TestPage.build` calls
    `ui.on("dstf_plot_visible", …)` after `client.connected()`, which adds a
    listener to the page's layout after the layout is drawn, so NiceGUI
    re-renders the layout (that call moved before `client.connected()`, in
    a scratch copy of 7bec0fd, logs it 0 times over the same two loads).
    The pending drawer's Esc avoids this by sending its event to an
    element built with its listener (`header.ESC_JS`).
  - After the header's Discard in a Test page's first ~1 s (the TRANSFORM
    gate: edits of two tests pending, an `or` tree with three leaves), the
    browser logs an uncaught "Resize must be passed a displayed plot div
    element." once per removed plot. Nothing visible breaks: the rebuilt
    cards draw. Cause: NiceGUI's plotly element (3.16) creates its
    ResizeObserver only after `await import("nicegui-plotly")` — over a
    second on a page's first plots — so a card the rebuild removed in that
    window gets an observer on its detached div, and `Plotly.Plots.resize`
    rejects it. Seen with a logging ResizeObserver over FakeLib: `observe`
    called on `c822` / `c824` with `isConnected` false ~1.3 s after they
    were removed, the rejection's stack running from NiceGUI's
    `plotly.js:12` into `Plots.resize`. Holding the module's request until
    after the Discard reproduces it every time. Not fixed:
    (a) an `unhandledrejection` listener dropping that one rejection passed
    6 of 6 runs, but suppresses by Plotly's message text; (b) a page-side
    wrap of `Plotly.Plots.resize` cannot be ordered ahead of the element's
    own import (3 of 6 runs still threw), and holding the plots until the
    wrap is in leaves a first-second race for a row opened then; (c) the
    tool's own copy of the plotly element with a connected check fixes the
    cause but forks a library file. The likely fix is upstream: a NiceGUI
    report that its plotly element should not observe an unmounted div.
    Likely the same first-second race as the rail-click Resize error of
    2026-09-26 above.
  - The Variables drawer on TRANSFORM's `TritiumExamples.HumTest` (76,992
    names, 64,407 rows, capped at 200) opens in ~1.2 s, and each menu
    change takes ~1.5 s. Over 6,089 names it is 0.6 s to open and 0.7-0.8 s
    per change.
  - A plain GET of `/` without a browser logs "app.storage.tab can only be
    used with a client connection" from `SuitePage.build` → `tab_state()`.
  - The Tests drawer's "Status, failures first" sorts within each package
    group when the list is grouped (the owner's Q1 A: "sorting sorts within
    each group"). Failures reach the top of the list only with Show flat.
- **Per-plot log or linear axes (owner, 2026-09-28; future, not built)**: a
  toggle on each plot for a log or a linear x axis, and one for its y axis.
  Logged with the owner's ruling O3 (the plots-from-the-tree plan's
  Corrections 2), under which a real array folds onto one plot whatever its
  members' scales: on a linear y axis a family spanning decades draws its
  small members flat.
- **Watch-only: the Julia drivers' result order (2026-09-29, from the U1
  review)**: `run_test.jl` and `run_persistent.jl` collect a result's
  variables in a `Dict{String, Vector{Float64}}`, so the `variables` object
  they write — "the file's order" a glob's matches and a test with no tree
  take their plot order from (D140 item 9) — is the Dict's hash order, not
  the model's unknowns-then-observables order. An `OrderedDict`
  (OrderedCollections; unverified whether `examples/julia/Project.toml`'s
  environment already resolves it) would make it the model's. Not changed:
  `julia` is not on PATH here, so the Julia-marked tests
  (`tests/test_julia_backend.py`) cannot run, and a .jl edit nothing
  executes is not verified. Change and run them where Julia is installed.
- **Watch-only: a result file deleted after a passed run reads as failed
  (2026-09-29, from the U1 review)**: the result reader hands a missing
  result file in as a failed `TestResult`, and `TestPage.run_state` reads a
  manifest entry that is not `pending` as a run that finished. So a test
  whose run passed, and whose `result.json` / `.mat` was deleted from the
  work dir afterwards, says "the run failed" on every line of its Test page
  (the strip, the count, the tree and the Variables drawer) — as the Suite
  row says sim failed. Not changed: nothing deletes a result file but a
  person, and telling the two apart needs the reader to say "no file"
  rather than "failed".
- **Watch-only: NiceGUI 3.16 replays a lost message after newer updates
  (2026-09-29, from the cold-open Test page bug, D141)**: on a reconnect
  of the same client, `Outbox.try_rewind` puts the messages the browser
  missed back into `outbox.messages`, and `Outbox.loop` sends the pending
  element `updates` FIRST and the replayed messages after them. A page that
  changed while its socket was down therefore ends on its OLDER state.
  Seen over a copy of a TRANSFORM run: the server logged `REWIND to 4 next
  5 history [4]`, and the browser received `update id=4` (the badge's PASS)
  and then `update id=5` (the replayed "Scoring…" paint), and stayed there
  until a reload. D141 keeps the UI loop from blocking past the ping budget,
  so the drop no longer happens on a cold open, but any real network drop
  can still leave a page stale this way. The fix is upstream: report it to
  NiceGUI with this sequence. Not changed here.
- **Watch-only: the structural-warning check skips a statistic missing on
  one side (2026-09-30, from U3, D143)**: `comparator.
  _check_structural_changes` compares `translation.nonlinear_count` and ten
  more structural statistics, and skips any that either side lacks. A
  baseline stored before the Dymola parser always wrote a list's summaries
  holds `nonlinear: []` and no `nonlinear_count`, so a run that now has
  three nonlinear systems draws no "Nonlinear system count" warning, while
  the Statistics card (which reads that count as 0 through
  `backends/common/statistics.py::read_summary`) calls the row changed.
  Reading the check's values through `read_summary` would close it. Not
  changed in U3: the plan left the check as it is.
- **A profile plot for scalar criteria over an array (owner, 2026-10-02,
  deferred while planning two-sided tolerance)**: a scalar criterion draws
  horizontal lines on its variable's time plot — the result's number, the
  baseline's, and the two it allows. That reads well for one level-type
  reduction (`max`, `min`, `final`, `mean`) on one variable. An array is
  one criterion per element on a shared family plot, so the same scalar
  over `T[1..10]` is up to forty lines over ten curves. The picture that
  fits is another plot kind: element index on x, the scalar on y, the
  baseline as a profile and each element's lower/upper tolerance as a band
  around it, so the element that is out shows at a glance. It is the
  "profile" plot kind the redesign roadmap lists as deferred; take it up
  when array criteria (3d: the slice field and save-time expansion) are
  planned (array criteria are built — D152, the add form's Indices and the
  folded criteria row; the profile plot is not). Nothing in the tolerance shape blocks it — each element's
  criterion carries its own number or pair. Beside it, to check in a
  browser: an `integral` scalar is in y-times-time units, and the scorer
  draws its lines on the y axis like any other reduction's; if they land
  off the curve and stretch the axis, an `integral` should draw none.
- **A warm, pre-imported server for the browser tests (2026-10-08, from the
  two-sided tolerance build, D147)**: every Playwright test of the tool
  starts its own `dstf author` process (`_served` in
  `tests/test_author_test_page_playwright.py`) and waits for it to serve.
  The shell's imports alone — NiceGUI, plotly, the shell — take 7 to 8 s
  here, and the two author files collect about 235 tests (215 in the
  Test-page file), so some 25 to 30 minutes of a run is processes
  importing the same modules; the Test-page file takes about an hour
  locally. None of it runs in CI (`quality.yml` deselects `playwright`),
  which D147 lists as a limit. One server process that has done its
  imports and forks, or re-opens a fresh project, per test would take most
  of that away. To settle first: what a test may leave behind in a shared
  process (the Session is one project per process; `tabstate` is the
  tab's), and whether a fork after NiceGUI's import is safe. Not built.
- **Staging off the UI loop (2026-10-08, from the two-sided tolerance
  build, D147)**: every keystroke in an editor field stages — a full merge
  and a rescore of the test — synchronously on the UI loop: 11 to 17 ms on
  a TRANSFORM-sized test, as measured during the build. `TypedNumber`
  (`authoring/typed_number.py`) took the visible damage away, the answer
  to one key landing under the next; two typing races are left that need
  keys closer together than a stage, so above a person's typing speed at
  that cost. A larger test, or a slower machine, lowers that bar. Staging
  on a worker with the newest value winning (a debounce is the cheap
  half) would remove the races rather than outrun them; E2's live drag,
  a stage per pointer move, will meet the same cost. Not changed: at
  today's cost no person reaches them.
  (E2 is built — D149: a held handle previews in the fields and stages nothing until its release.)
- **Pin the axis ranges while a criterion is open (2026-10-08, from the
  two-sided tolerance build, D147)**: opening a criterion adds its arrows
  to the figure, and plotly.js autoranges an axis over its shapes, padding
  a pixel-sized path by its extent. With tolerances that reach past the
  data, opening the criterion therefore rescales the plot — 14.6 % off the
  value axis's scale on a 234 px axis (Chromium) — and closing it scales
  it back, so the curve moves under the eye at the moment it is being
  read. Setting each axis's range to what it was just before the open,
  widened only as far as an arrow would otherwise leave the plot, would
  hold the picture still; a zoom or a double-click reset then has to mean
  the same as it does on a closed plot. Beside it: the arrows of an edge
  at the plot's own edge are drawn in the margin by a stylesheet rule that
  needs CSS `:has` (D147's limits), which a pinned range with a margin
  would not.

- **Sampling a baseline by region (owner, 2026-10-11; deferred to its own
  session — grill before planning)**: the simple half is built (D153) —
  a baseline keeps every point the simulator wrote unless `baseline_points`
  gives a number. What is deferred is the flexible half the owner described:
  uniform sampling, chosen times, or a different density in different parts
  of a run (sparse at the start, fine in the middle or around events). A
  candidate form, NOT a decision: a list of segments in place of the number,
  `[{"until": 50, "points": 20}, {"until": 200, "points": "all"},
  {"points": 200}]`. What the first look found, to be argued in that session:
  - Three things are easy to conflate: what the simulator writes
    (`number_of_intervals` / `output_interval`), what the baseline keeps
    (this), and where scoring looks (a leaf's `window`). "Do not sample the
    beginning" is a scoring choice a window already makes VISIBLY; made by
    sampling it leaves a region silently unchecked, which is what the old
    tail-trim did. Candidate rule: no region may hold zero points — every
    region keeps its end points and its events.
  - `nrmse` is a mean over the baseline's POINTS, not over time, so a denser
    region weighs more in the score. "Fine in the middle" is therefore a
    weighting; three windowed leaves with their own tolerances say "stricter
    in the middle" directly and exist today. A time-weighted nrmse would
    remove the effect and change every stored score.
  - Other leaves read the grid too: a `scalar` max or integral is taken from
    the stored baseline (a thinned peak is a different target), a
    `dominant-frequency` check cannot see above what the stored spacing
    resolves, `event-timing` needs the event points (always kept).
  - Candidate rule: select, never interpolate — a baseline holds only
    samples the simulator wrote; "uniform" means the written samples nearest
    a uniform grid, and true uniform output is `output_interval`'s job.
  - Thinning is even by index and can drop a spike. A shape-preserving
    thinning (LTTB, as the figures use) picks different indices per
    variable, and a baseline has ONE time vector for all its variables —
    that is a change to the reference JSON layout.
  - "Around events" cannot be written as times beforehand: events are known
    only after the run, so it would be its own option.
  - Measured on TRANSFORM (330 baselines): 11 were thinned by the old 2000
    cap, the median run has 502 rows, and keeping everything takes the
    baselines from 10.7 MB to about 24.6 MB, two 74,000-row tests being
    about 5 MB each — so segments would mostly buy file size, and the size
    is small. The open question to start from: is the per-region wish about
    what is SCORED or about what is STORED?
  Beside it, not built: a baseline whose last time is earlier than its
  recorded stop time could be reported on compare (two stored before the
  2026-07-06 tail fix ended short until they were accepted again); and a
  compare could say when a baseline was stored under another
  `baseline_points` than the one now in force.

- **Small things the release gates saw and left (2026-10-11)**: a cancelled
  run's "stopped at t = …" note is anchored in the legend's band and can
  overprint a legend entry; a cancelled test's Suite row says "No simulation
  results found" under the status CANCELLED while its page says where it was
  cancelled; after a plain `compare`, a test with no result can still link to
  a report page left by an earlier `--report`; a failed run's own sentence
  ("exceeded 6.0 s") does not survive a restart, only its recorded verdict
  does — keeping it means one more stored field per run.

- **Known limits the last review rounds left (2026-10-12)** — each was seen
  by running it, none was built:
  - A number too large for a float (hundreds of digits) inside a table row —
    a `points` row's `time` / `value`, a `dominant-frequency` peak's `freq`,
    a `tube_points` row, an `events` row's `time` — is still a traceback out
    of `compare`; everywhere else in a spec entry it is the one-line config
    error. The fix is row validation where the mode is built.
  - A stored baseline whose `comparison` is `null`, or a soft check of the
    wrong shape, is a traceback after the simulations ran; a baseline file
    cut short reads as no baseline (`PASS [new]`). `run`'s check before it
    simulates covers an override nothing reads, not these.
  - `run --parallel N` over FMUs can fail tests with `XMLSchemaParseError`:
    FMPy's model-description reader is not safe across threads. Sequential
    runs are.
  - `tests/test_baseline_points.py` and `tests/test_recognizer.py` each fail
    five tests when run alone and pass in the whole suite (a registration
    another test file's import makes).
  - An interrupted `--parallel` run can leave a test that finished after the
    interrupt as `done` in `status.json` and `NOT RUN` at the next compare.
  - Dymola resolves a dependency given by NAME (`"Modelica"`) against the
    working directory; give a path where the name is not one Dymola knows.
  - `find_class` records a `redeclare model X = …` written inside a
    modification at its bracket depth and passes it over as a class of the
    body; the scan of a file is about three times slower for it (18 ms on a
    650 kB file).
  - A watch plot draws the run only: no baseline and no companion overlay.
    The example's `analytic` companion holds `x` only.
  - **`dstf author` cannot give a test its FIRST criterion** when the test
    tracks no variable (what plain `dstf add <model>` writes): the page says
    "nothing is scored" and offers no `+`. The add form builds on a tree or
    on what the flat path scored, and there is neither. The way round is
    `dstf add <model> --variables <pattern>`, `spec-update`, or a hand edit.
    Building it means a tree made from nothing in the Session (a `metrics`
    of one leaf, which then tracks its variable) and a `+` on a page with no
    criteria — a new path through the authoring core, to be gated in a real
    browser. First candidate after v1.0.
  - A test that lost a tracked variable is locked in the tool until it is
    queued again: every row says "its last run no longer holds what it
    scored — queue it again" and has no +/wrap/−.
  - A Suite view stored in the browser with values of the wrong TYPE (a
    number where a list is read) still empties the table; no page version
    writes one. Clearing the site data restores it.
  - `dstf add` on a Python-source library writes no `source`, and the test
    then fails to load; the entry needs its `source` by hand.
  - OpenModelica refuses a library whose root directory is a link of
    ANOTHER name (`Expected the package to have name …`); a link of the
    same name works.
  - A Save in the tool on a flat test writes the `metrics` tree and leaves
    the old `comparison.variable_overrides` beside it, where it scores
    nothing; the preview shows neither that the tree is new nor that the
    override stays.
  - The report page says "leaves" in four places (`3 / 3 leaves passed`, a
    tooltip, the tree's heading, the Worst NRMSE note) where the tool and
    the console say criteria.
  - `run -i` prints `N/N passed, 0 failed` for a test that FAILS with no
    failing criterion (a `weighted` group under its threshold), and
    `1/2 passed` for a PASS through one branch of an `or`.
  - The cancelled-run marker's label is cut at the plot's left edge when
    the stop is near t = 0.
  - Nothing here was run on Windows in this round: no Windows baselines
    ship for the OpenModelica, Python or Julia examples.
  - The hint for a baseline entry stored under expression text quotes the
    expression as scoring normalizes it, not the text the file holds (a
    stored `alphas[\n 1, 1]` is quoted as `alphas[1, 1]`), and does not give
    the entry's `index`; the helper is still called `migration_hint` though
    no command migrates anything. No shipped or known baseline is stored
    that way.

- **Review and revisit everything a user reads (owner, 2026-10-13, after
  the 1.0 release)**: the CLI — its commands, flags and help text — the
  usage documentation, and Sphinx documentation, carefully and as a whole,
  as a user meets them. The owner's example: `--dymola-interface` exists in
  the CLI. As the tree stands, that flag is a global one, offered on every
  command of a tool that is meant to be backend-agnostic (`cli/main.py`),
  and there is no Sphinx tree: the documentation is the Markdown files under
  `docs/` and the README. Whether Sphinx is to be built, and what the review
  should conclude about backend-specific flags at the top level, are open —
  grill before planning.

- **Evaluate when, and whether, parts or all of this project should move to
  Rust (owner, 2026-10-13)**: an evaluation, not a decision. Nothing has
  been measured for it yet; which parts, on what evidence and at what cost
  to the plug-in contracts are the questions. Grill before planning.

- **Review the authoring interfaces against the original prototype (owner,
  2026-10-13, from testing `dstf author` by hand)**: the owner sees issues
  in manual use — the tool no longer works the way the old, original
  prototype HTML did. Which behaviours are meant has not been written down
  yet: the first step is the owner's list, taken with the prototype and the
  tool side by side, before anything is planned. (A reading, not the
  owner's words: the prototype is the editable report page from before
  editing moved into `dstf author`, D125.) Grill before planning.
