# Future Ideas

## Interactive setup wizard

- Guided terminal flow for creating or editing `testing.json`
- Prompts for: library path, simulator selection (scan for installed Dymola versions), dependencies, reference root location
- If `testing.json` already exists, offer to edit fields interactively
- Reduces onboarding friction for new libraries — no need to hand-write JSON

## Test discovery by `extends` or folder

- Find testable models by scanning for classes that extend a specific base (e.g., `extends Modelica.Icons.Example` or a custom test icon)
- Alternatively, discover all models within a specific package/folder (e.g., everything under `MyLib.Examples.*`)
- Complements UnitTests and test_spec: UnitTests requires in-model instrumentation, test_spec requires manual enumeration, extends-based discovery is automatic
- Could generate a test_spec.json from discovered models as a starting point

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

## Filtered interactive review

- Run all tests but only prompt for specific categories: `--interactive=failed`, `--interactive=no-baseline`, `--interactive=warnings`
- Passing tests are silently accepted/skipped without user interaction
- Avoids pressing `s` dozens of times to get to the few tests that need attention
- Could combine filters: `--interactive=failed,no-baseline` to review both failures and new tests
- Default `-i` behavior stays the same (prompt for everything)

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

## Full reference data representation in HTML reports

- Diagnostic final values (total CPUtime, total EventCounter) should appear in the statistics table, not just as plot trajectories
- More broadly: every field in the reference JSON should be represented somewhere in the HTML — metadata, simulation params, statistics (including diagnostic finals), variable comparisons, and diagnostic trajectories
- Currently the statistics table shows dslog-parsed stats but not the diagnostic finals stored at the top level of `statistics` (e.g., `"CPUtime": 12.3`, `"EventCounter": 42`)
- These top-level stats should be added to the statistics table with ref vs current comparison and change highlighting
