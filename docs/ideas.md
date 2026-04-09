# Future Ideas

## Priority Matrix

Ideas ranked by implementation ease and user impact. Ease: L (days), M (week), H (weeks+). Impact: how much it improves the daily workflow.

| # | Idea | Ease | Impact | Notes |
|---|------|------|--------|-------|
| 1 | ~~Filtered interactive review~~ | L | High | **DONE** — `-i [FILTER]` with categories: `failed`, `no-baseline`, `warnings`, `sim-failed`, `passed`, `all` |
| 2 | Link to simulation artifacts from HTML | L | High | Just adding `file://` links to existing reports |
| 3 | Full reference data in HTML reports | L | Medium | Display fields already stored, just not rendered |
| 4 | Manifest compaction / ID reset | L | Low | Niche — only needed after major restructuring |
| 5 | Condensed HTML with progressive disclosure | M | High | `<details>`/`<summary>` restructure of existing HTML |
| 6 | Auto-generate HTML report suite | M | High | Plotly integration + index page; eliminates `-i` + `p` workflow |
| 7 | Configurable variable ordering | M | Medium | Config plumbing + sort logic in reports and references |
| 8 | Interactive tolerance editing | M | Medium | Requires Plotly; slider + writeback to reference JSON |
| 9 | One-click "open in Dymola" | M | Medium | .mos generation straightforward; protocol handler is platform-specific |
| 10 | Interactive setup wizard | M | Low | Nice onboarding but power users skip it quickly |
| 11 | Test discovery by extends/folder | H | High | Requires Modelica AST parsing or robust regex scanning |
| 12 | Model health analysis from reference data | H | High | Mining + ranking logic across all refs; powerful but complex |
| 13 | Dependency-aware test ordering | H | Medium | Requires dependency graph extraction from Modelica sources |

**Recommended order**: Start with 2-3 (quick wins, 1 is done), then 5-6 (report overhaul as a batch), then 11-12 (high-effort, high-value).

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

## Auto-generate HTML report suite with navigation

- A `--html` or `--report-dir` flag on `run`/`compare` that generates comparison HTMLs for all tests in one go, plus an index page linking to each
- Index page shows pass/fail summary table with links to per-test comparison pages
- Eliminates the need to use `-i` and press `p` per test just to get plots
- Consider alternatives to static HTML:
  - **Plotly**: interactive plots (zoom, pan, hover for values) embedded in HTML — no server needed, just larger files
  - **Bokeh**: similar to Plotly, slightly heavier
  - **Panel/Dash**: full web app with server — overkill for this use case
  - Plotly is likely the best fit: interactive plots in self-contained HTML, matplotlib stays as the lightweight fallback
- Could also explore a single-page app approach: one HTML file with a sidebar listing all tests, clicking loads that test's plots inline (avoids many separate files)
- Include a mapping table showing test_NNNN (working directory) ↔ ref_NNNN (reference file) ↔ model ID, so the user can navigate between simulation artifacts and references easily

## Condensed HTML report with progressive disclosure

- Current HTML reports show every field (translation stats, simulation stats, all variables) in a flat layout — gets overwhelming for complex models with dozens of stats and variables
- Redesign to show a concise summary by default with the most important metrics:
  - **Top-level**: pass/fail, NRMSE worst-case, continuous states, nonlinear system count/max, CPUtime, event count
  - **Variable table**: just name, pass/fail, NRMSE — not full trajectory details
- Everything else available via expand/collapse (`<details>`/`<summary>` HTML elements):
  - Full translation statistics (original model, translated model, initialization)
  - Full simulation statistics
  - Per-variable detail panels (error plots, difference plots, segment breakdown)
  - Raw system size lists (nonlinear/linear sizes)
  - State names list
- Could also use a tabbed layout: "Summary" | "Translation" | "Simulation" | "Variables" tabs in a single page
- For the auto-generated report suite (see above), the index page should be the condensed view — click through to per-test detail pages
- Goal: a library maintainer scanning 300 test results should be able to spot problems in seconds, then drill into specifics only where needed

## Link to simulation artifacts from HTML reports

- For tests that fail to simulate (no .mat produced), the user needs to inspect `dslog.txt`, `translation_log.txt`, or `dsin.txt` to diagnose the issue
- HTML reports should include `file://` links to these artifacts in the per-test working directory (`testing_output/.../test_NNNN/`)
- Links to show: `dslog.txt`, `translation_log.txt`, `dsin.txt`, `dsfinal.txt`, the `.mos` script, and the `.mat` file (if it exists)
- For failed tests, the dslog link should be prominent — that's the first thing a user looks at
- For passing tests, links are still useful for inspecting simulation details but can be less prominent (e.g., in a collapsible section)
- Also useful in the auto-generated report suite index page: a "Files" column with quick links per test

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

## Full reference data representation in HTML reports

- Diagnostic final values (total CPUtime, total EventCounter) should appear in the statistics table, not just as plot trajectories
- More broadly: every field in the reference JSON should be represented somewhere in the HTML — metadata, simulation params, statistics (including diagnostic finals), variable comparisons, and diagnostic trajectories
- Currently the statistics table shows dslog-parsed stats but not the diagnostic finals stored at the top level of `statistics` (e.g., `"CPUtime": 12.3`, `"EventCounter": 42`)
- These top-level stats should be added to the statistics table with ref vs current comparison and change highlighting
