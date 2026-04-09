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
| 8 | Interactive tolerance editing | M | Medium | Requires Plotly; slider + writeback to reference JSON |
| 9 | One-click "open in Dymola" | M | Medium | .mos generation straightforward; protocol handler is platform-specific |
| 10 | Interactive setup wizard | M | Low | Nice onboarding but power users skip it quickly |
| 11 | Test discovery by extends/folder | H | High | Requires Modelica AST parsing or robust regex scanning |
| 12 | Model health analysis from reference data | H | High | Mining + ranking logic across all refs; powerful but complex |
| 13 | Dependency-aware test ordering | H | Medium | Requires dependency graph extraction from Modelica sources |

**Recommended order**: 1-3, 5-6 are done. Next: 11-12 (high-effort, high-value), or 7-9 (medium effort).

---

## Interactive setup wizard

- Guided terminal flow for creating or editing `testing.json`
- Prompts for: library path, simulator selection (scan for installed Dymola versions), dependencies, reference root location
- If `testing.json` already exists, offer to edit fields interactively
- Reduces onboarding friction for new libraries — no need to hand-write JSON

## Test discovery by `extends` or folder with interactive selection

- Find candidate test models by scanning for classes that extend a specific base (e.g., `extends Modelica.Icons.Example` or a custom test icon)
- Alternatively, discover all models within a specific package/folder (e.g., everything under `MyLib.Examples.*`)
- Complements UnitTests and test_spec: UnitTests requires in-model instrumentation, test_spec requires manual enumeration, extends-based discovery is automatic
- Could be a CLI command like `modelica-testing find-tests --extends Modelica.Icons.Example --package MyLib.Fluid`
- **Interactive selection mode**: present discovered candidates in a checklist, user selects which to add to `test_spec.json` — avoids manually writing JSON for dozens of models
- Could show which candidates already have UnitTests or test_spec entries (skip those)
- Variable selection: offer `["*"]` (track all), `[]` (simulate only), or prompt for specific patterns per model

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

## Interactive tolerance editing and tolerance tubes

- In interactive plots (Plotly or similar), allow the user to adjust the NRMSE tolerance via a slider or input field and see pass/fail update live
- Push the modified tolerance back to the reference file as a per-test or per-variable override
- **Tolerance tubes**: display upper/lower bounds around the reference trajectory, defined by the tolerance. Visually shows where the actual signal is within/outside acceptable range
- Could support per-variable tolerances (e.g., temperature variables need tighter tolerance than pressure) stored in the reference JSON alongside each variable
- Global tolerance remains the default; per-variable overrides in the reference take precedence

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

## ~~Full reference data representation in HTML reports~~ (DONE)

- **Implemented**: All reference JSON fields now represented in HTML
- Added `status` and `date_added` to metadata table
- Diagnostic finals (CPUtime, EventCounter) were already merged into simulation stats table with change highlighting
- Fixed metadata table column header from "Simulation" to "Current"
