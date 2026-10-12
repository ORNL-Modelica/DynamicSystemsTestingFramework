# CLAUDE.md

## Project Overview

**Dynamic Systems Testing Framework (DSTF)** — formerly **ModelicaTesting** — is a Python framework for regression and unit testing of time-dependent system behavior across simulated and pre-recorded trajectories.

**Current state**: a multi-backend regression harness. Discovers tests by scanning `.mo` files and/or reading `test_spec.json`; simulates via **Dymola** (Python interface default, batch `.mos` fallback), **FMPy** (prebuilt FMUs), **OpenModelica** (OMPython persistent-worker default, `omc` batch fallback), **Julia/ModelingToolkit** (persistent `julia` worker), or **Python** (subprocess-per-test; any `simulate(stop_time, tolerance) -> dict` — scipy, pandas, CSV, HTTP, etc.); scores via a composable **MetricTree** (leaves: `nrmse` / `tube` / `points` / `range` / `event-timing` / `dominant-frequency` / `scalar`; combinators: `and` / `or` / `k-of-n` / `warn` / `weighted`; multi-baseline via `"against"`; time-windowed leaves via `"window"`). Reports via ONE suite-wide HTML surface — the dashboard (also what `--report-format html --output` writes) — plus per-test **read-only Plotly HTML** (figures built in `dstf.figures` from the geometry each metric emits; server-rendered metric tree with the CLI's verdict, criterion and detail pairs; overlay picker and error overlays toggle traces already in the figure, the error overlays on two labelled error axes — the page computes nothing) + JUnit XML. Criteria are authored in `dstf author`, the Python-side tool over the same figure layer (D125): a Suite page and a Test page whose criteria are edited inline beside the plot they score (D131), where a tree node's forms open as a row under that node and a plot's legend holds its signals while each criterion's drawing is shown or hidden from its own row (D133), and a 40 px rail opens a Tests drawer (switch test) and a Variables drawer (D134) that adds context series to the plot you selected or makes a watch plot of one variable — the plots themselves are derived from the criteria tree, and `plots` holds only those extras (D140). References partitioned under `<reference_root>/<Backend>/<os>/` with three baseline roles: **primary** (regression anchor, hard-fail), **soft_checks** (warn-wrapped cross-regression imports), **companions** (plot-only overlays, including cross-library).

**Forward direction**: [docs/vision.md](docs/vision.md) lays out the six-layer plug-in architecture (Source → Discovery → Backend → Dataset → Metric → MetricTree). See [docs/architecture.md](docs/architecture.md) for the layer ↔ code mapping and [docs/extensibility.md](docs/extensibility.md) for plug-in contracts.

**Project history**: [docs/decisions.md](docs/decisions.md) is the authoritative log (D44–D79 covers the work summarized above: Phase 1 abstractions, Phase 2 FMPy, Phase 3 MetricTree, Phase 4 multi-baseline + cross-backend + weighted, Phase 5 PTA recognizers, Phase 6 interactive reporter MVP + baseline-role split, D69–D70 OpenModelica, D77–D79 Julia/MTK, D80 Python-driven tests, D81 rename to DSTF, D125 the two-tier split, D131 the Test page, D132 variable identity, D133 the form row and legend, D134 the rail and drawers, D139 the Test page, D140 plots from the tree, D147 two-sided tolerance, D148 themes, D149 on-plot editing, D150 the simulation queue, D151 live results, D152 a criterion over an array family, D153 the baseline's points). Do not re-summarize past phases in this file — grep `decisions.md` or `git log` when context is needed.

## Project Structure

```
DynamicSystemsTestingFramework/
├── src/dstf/                     # Python package (src layout)
│   ├── cli/                      # CLI package: run, compare, discover, author, export, add, manifest, spec-update,
│   │                             #      companion, soft-check, import-baseline, export-schema,
│   │                             #      check-dymola / check-openmodelica / check-julia,
│   │                             #      migrate-variable-names (one-shot; deleted at the v1.0 tag)
│   ├── config.py                 # Config dataclass, path resolution, testing.json loading, auto-detect backend
│   ├── pipeline.py               # run_pipeline — the run as a library callable; cmd_run is its CLI wrapper
│   ├── discovery/                # Recognizer registry, JSON recognizer, test_spec parse, plots (a test's plots, derived from its criteria, + the `plots` extras key), RFC 6902 patch_apply,
│   │                             #      patch_envelope (both envelope forms), spec_update (the shared read-modify-write),
│   │                             #      unit_tests.py (the ONE place a UnitTests variable is named: <instance>.x[i] + its expression),
│   │                             #      scan_cache (the tool's scan cache under $XDG_CACHE_HOME/dstf/scan/)
│   ├── backends/                 # Backend ABC + Dymola / FMPy / OpenModelica / Julia / Python backends
│   │   ├── common/               # Shared: mat_reader, live_mat.py (a result file read while its run writes it), tree-kill, result variables, JSON-driver base, result_files (any variable of a result file),
│   │   │                         #      statistics.py (the zero-count rule: a system-size list's summaries, written and read in one place)
│   │   ├── pool.py               # SimulationPool: a FIFO queue of tests over a backend's runners that stays open (the tool's queue)
│   │   └── cross_backend.py      # EXPERIMENTAL Dymola→FMU→FMPy baseline chain
│   ├── comparison/               # ComparisonMode registry, MetricTree (metric_tree.py: the combinators) spec/eval, validator, tree_spec helpers,
│   │                             #      mode_schema (derive_schema — JSON-Schema export + the tool's forms),
│   │                             #      handles.py (edit_handle — a dragged handle back to a raw leaf),
│   │                             #      sides.py (Sides — the ONE place a tolerance's number-or-pair is read or written),
│   │                             #      snap.py (the one snap rule: a value the pointer placed is written at a readable step, D149)
│   ├── storage/                  # ReferenceStore + roles mixin, RefIndex, Baseline, accept (the one accept path),
│   │                             #      migrate_names.py (the `migrate-variable-names` rewrite; deleted at the v1.0 tag)
│   ├── authoring/                # Session, capability contract, spec document — the tool's core;
│   │                             #      layout.py (the plot extras' edits as pure functions, one op on /plots),
│   │                             #      variable_rows.py (the Variables drawer's rows as data),
│   │                             #      picker_rows.py (the add form's variable picker as data: sections, filter, cap),
│   │                             #      family_rows.py (a family's folded criteria row and its Indices as data, D152),
│   │                             #      suite_rows.py (a Suite row — the row model's TestRow — from the Session),
│   │                             #      queue_rows.py (the simulation queue as data: a row per item, the header's indicator),
│   │                             #      live_rows.py (a running test's page as data: reader snapshots to the series a plot draws, the words of each state)
│   │                             #      + live_runs.py (the Session's readers of the running tests' result files, and unfinished runs' partial results; D151),
│   │                             #      situation.py (a test's situation and the strip's sentence), spec_preview.py (an entry's
│   │                             #      diff as test_spec.json would write it), needle.py (the drawers' budgeted filter matcher),
│   │                             #      typed_number.py + typed_number.js (the tool's number field: it keeps what is typed until it is left),
│   │                             #      unscorable.py (what an editor refuses to stage: a negative tolerance, bounds out of order),
│   │                             #      errors.py (which failed acts read as their plain sentence: USER_ERRORS), tree_move.py (a move within a test's tree, D144),
│   │                             #      shell/ (the NiceGUI tier: the process, the Suite and Test pages,
│   │                             #      suite_grid.py + suite_grid.js (the Suite table: AG Grid, driven only by transactions),
│   │                             #      test_list (the Tests drawer's list), tree_card + plot_card, stats_card.py (the Statistics card),
│   │                             #      queue_panel.py (the Suite page's queue panel and the header's queue indicator),
│   │                             #      live_view.py (the Test page's part of a running test's view: what its drawn plots ask for and re-send),
│   │                             #      rail.py + variables_drawer.py (the Test page's Tests and Variables drawers),
│   │                             #      picker.py (the add form's variable picker over picker_rows),
│   │                             #      family_fold.py (a plot card's folded criteria rows over family_rows),
│   │                             #      drawer_controls.py (both drawers' filter box and menus), strip.py (the Test page's status strip),
│   │                             #      grips.js (a plot's tolerance arrows: which show, which shapes take the pointer, what a release sends),
│   │                             #      placing.py (the open editor's ⌖ buttons and its one armed field, D149),
│   │                             #      the header, tab_theme.py (the tab's theme and its live switch),
│   │                             #      actions.py — ActionBar (a button + dialog) and InlineHost (panels drawn inline)),
│   │                             #      forms.py, builtin/ (edit criteria + edit family + edit tree + accept + enqueue + edit on plot)
│   ├── figures/                  # VariableView (view) → plotly figure dicts (plotly: variable_figure, plot_figure) + array families + palette.py (what a figure takes from a theme, by name) + LTTB decimate; both tiers draw from it
│   └── reporting/                # status.py (the classifier + every status word and class) + rows.py (the row model both HTML surfaces render: TestRow, COLUMNS)
│       │                         #      + tree_rows.py (a plot's criteria rows with their crumbs — the one walk both tiers list them by)
│       │                         #      + stats_rows.py (the Statistics rows both tiers render: the report's table and the card; one "changed")
│       │                         #      + console/junit + context_builder + report_suite + dashboard_render + overlay_loader (companion / soft-check overlays) + schema_export (`export-schema`) + theme.py (the token tables both tiers render, one per bundled theme: default and dark)
│       └── templates/            # interactive.html + axes.js + report.js (INTERACTIVE_JS_PARTS, copied out as interactive.js) + dashboard.* + filters.js (the typed column filters)
│                                 #      + vendor/plotly.min.js (offline-capable reports)
├── examples/
│   ├── modelica/ModelicaTestingLib/   # Demo Modelica library (UnitTests component + 10+ showcase tests)
│   ├── fmu/                           # Reference-FMUs demo (BouncingBall / Dahlquist / VanDerPol)
│   ├── julia/JuliaMtkTestingLib/      # Julia/MTK demo library (mirrors ModelicaTestingLib layout)
│   └── python/PythonTestingLib/       # Python demo library (SimpleRamp via scipy, ConstantCsv data loader)
├── tests/                        # pytest suite (~6500 tests, ~350 of them the Playwright subset, gated by importorskip)
├── docs/                         # decisions.md, vision.md, architecture.md, qa/reporter_checklist.md, ideas.md
├── scripts/                      # fetch_reference_fmus.py, smoke_test_dymola_export.py
├── pyproject.toml                # uv/hatchling config; optional extras: [dev], [author], [fmpy], [om]
└── CLAUDE.md
```

## Running the Tool

The package ships a console script (`[project.scripts]` in `pyproject.toml`). The canonical dev invocation is `uv run dstf ...`. `python -m dstf ...` is supported as a fallback (both call `cli.main_entry`). End users will install via `uv tool install dynamic-systems-testing-framework` (or `pipx install`) and run plain `dstf` — the distribution name differs from the import name because `dstf` on PyPI belongs to an unrelated project (D124). Nothing is published yet, so that command does not work today.

```bash
# With testing.json containing source_path — single entry point
uv run dstf --config path/to/testing.json run

# Global flags work on either side of the subcommand (dstf run --config x == dstf --config x run)

# Or with explicit flags
uv run dstf --source-path /path/to/MyLib --reference-root /path/to/refs run

# Interactive review (accept/skip/plot per test)
uv run dstf --config testing.json run -i
uv run dstf --config testing.json run -i failed
# Categories: failed, no-baseline, warnings, sim-failed, passed, all

# Accept all results as new baselines
uv run dstf --config testing.json run --accept

# Generate per-test interactive deep-dive plots. The unified dashboard.html always exists; --report (a flag: it takes no path) adds <work_dir>/reports/ref_NNNN/interactive.html per test (test_NNNN for a test with no baseline).
uv run dstf --config testing.json run --report

# Parallel run (--parallel on compare = render threads). --batch-size is read by Dymola's --batch path only: small batches, queue-dispatched
uv run dstf --config testing.json run --parallel 4
uv run dstf --config testing.json run --batch --parallel 4 --batch-size 3
# Live progress + final report: work_dir/dashboard.html (one page; re-rendered on every state change and meta-refreshed by the browser every 5s during the run, becomes the static report after compare)

# Filter accepts: glob, comma-separated list, or @file (one pattern per line, # comments)
uv run dstf --config testing.json run --filter "Foo.A,Foo.B"
uv run dstf --config testing.json run --filter @rerun.txt

# Incremental rerun + full merged report
uv run dstf --config testing.json run --filter @failed.txt --merge --report
uv run dstf --config testing.json run --rerun failed,sim-failed --report

# Compare without re-running simulations (uses last results)
uv run dstf --config testing.json compare

# Machine-readable reports to a chosen path (--output works on run and compare)
uv run dstf --config testing.json run --report-format junit --output results.xml
uv run dstf --config testing.json compare --report-format html --output report.html

# Apply an RFC 6902 JSON-Patch envelope against test_spec.json — single- or
# multi-model form, from any source
uv run dstf --config testing.json spec-update spec_patch.json

# The authoring tool: a browser shell over the last run (needs the [author] extra).
# The Suite page is a table of the tests (count pills, typed column filters with .* / Aa, package
# group rows, a column chooser); Accept the selection's results as baselines, Queue the selection:
# one simulation queue per tool process (D150), first in, first out, over workers that stay alive.
# Its panel beside the table lists each item in run order (Remove a waiting one, Cancel a running
# one, Open test / Accept an ended one; a Workers field, Clear finished, Stop workers); the header
# says `3 running · 5 queued` on every page. A queued or running test stays editable; each result
# is read back and that test rescored as it ends. A running test's page keeps its plots and draws
# the trace as the simulator writes it (Dymola, OpenModelica; D151): `t = 42 / 100 s` in the strip,
# `waiting for result` on every criterion. A run that was cancelled or timed out shows what it
# wrote, marked where it stopped, and is never scored or accepted.
# Open a test: a Statistics card above the tree (every recorded statistic, run vs baseline,
# collapsed to the changed ones; `+` adds a scalar criterion, and a statistics criterion is
# edited there), its criteria tree closed to one line naming what failed, its plots — one per scored
# variable or array in tree order, then watch plots (D140) — stacked and drawn as they scroll into
# view, each with its criteria rows in tree order led by their ancestors (`and › k-of-n ›`),
# a criterion's editor under its row (drag its handles on the plot above; a tolerance is two
# fields with a link toggle — linked writes one number, unlinked a lower/upper pair — and has an
# arrow outside each side on the plot, shown while the pointer is near its point, D147;
# Ctrl+click on the plot adds a row that settles on the data, a field's ⌖ arms it for the next
# click, the fields follow a held handle, and every placed value is snapped to its axis, D149),
# a status strip under the test's row saying its situation and filling the one next action,
# +/wrap/− on every tree node; the open tree draws a bar per level, folds its groups (the tab's,
# per test), and a row's click opens its criterion's editor or a k-of-n's / weighted's fields under
# it, its ⤓ showing a criterion where it is drawn (D146); each form an inline row under its
# node: a `+` adds right after a criterion, last under a combinator, or from a plot's criteria
# header; a combinator's kind is a dropdown in its row (unwrap in it); a ⋮⋮ grip or Move to… moves
# a criterion or group: drop on the top half of a row to put it before, on the bottom half to put it
# in (or, on a criterion, to wrap both in `and`). The add form picks from
# EVERY variable of the result file and the run's diagnostics (a searchable picker; the Metric
# list follows the choice); a criterion's variable is tracked by that, and the strip asks for a
# new run where the last run did not record it. An element of an array picked there takes
# Indices (`1:5:20`, `1:3,7`; a count beside it and on Add): one criterion per chosen element.
# Under a family's plot identical criteria fold into ONE row (`tube · pipe.T[1:20]`, `18 pass ·
# 2 fail`) whose editor changes them all and whose Indices add or remove elements; its chevron
# unfolds it to its members, and an element with its own criterion follows them (D152).
# An editor's field says the tolerance in effect
# (the test's / baseline's / project's) under it. Focus shows one plot alone,
# a select switches plots. Variable labels are cut in the middle (head…[3]).
# A plot's legend holds its signals, above the plot; a swatch on each criteria row shows or hides
# that criterion's drawing; one Error select per plot of one variable picks the error series.
# The pending count opens the preview: each edited test's entry before and after, as a diff.
# A rail down the left opens the Tests drawer (switch test) or the Variables drawer (a context
# series on the selected plot, or a watch plot); `×`, Remove plot and Reset layout take them
# away. Both drawers carry one control set: filter with .* / Aa; Tests has sort and names menus,
# a group toggle and status pills (a Full name is cut at its start); Variables has sort, group
# and names menus. A drawer resizes by its right edge (drag, or ←/→ on the focused grip).
# The tool opens in testing.json's `theme`; the header's ⋮ names the other one (Dark theme /
# Default theme) and switches this browser tab, live — its plots keep their zoom and edits.
uv run dstf --config testing.json author [--port N] [--no-browser]

# Baseline-role CLIs
# --map (repeatable) records which of the companion's variables draws on which of the
# test's — checked against both files at add time, and the only thing the report reads
# for a mapped companion. Unmapped ones match by name, then by expression.
uv run dstf --config testing.json companion add <model> <name> <path> [--map TEST_VAR=COMPANION_VAR]
uv run dstf --config testing.json soft-check list <model>
uv run dstf --config testing.json import-baseline <model> <name> <source>   # soft_checks only; companions via companion add

# One-shot migration (D132): stored baselines and test_spec.json keys move from a
# UnitTests variable's expression text to its result-file name (`unitTests.x[i]`).
# Dry-run by default; DELETED AT THE v1.0 TAG.
uv run dstf --config testing.json migrate-variable-names            # report only
uv run dstf --config testing.json migrate-variable-names --apply

# Manifest / schema
uv run dstf --config testing.json manifest dump
uv run dstf --config testing.json manifest cleanup [--orphans] [--apply]   # dry-run by default
uv run dstf --config testing.json export-schema --output schema.json

# Persistent workers are the DEFAULT for Dymola and OpenModelica and Julia.
# Force the legacy batched backend (.mos / omc -s / per-test julia subprocess)
uv run dstf --config testing.json run --batch --parallel 4 --report

# Diagnose Dymola Python interface discovery
uv run dstf check-dymola
```

## Running Tests

```bash
uv sync --extra dev --locked       # One-time: build .venv from uv.lock (what CI runs)
uv run pytest                      # Run the suite
uv run pytest -m "not playwright"  # Skip browser-driven interactive-HTML tests
```

`uv sync` installs the project editable, exactly like `uv pip install -e`, but
pins every dependency to the same `uv.lock` CI uses. (Not the same *versions* —
the lock resolves different numpy/scipy on 3.10 than on 3.12, and neither the
repo nor CI pins an interpreter.) `--locked` makes it fail rather than silently
re-lock when `uv.lock` has drifted from `pyproject.toml`; if you change a
dependency, run `uv lock` and commit `uv.lock`. Skip that and CI fails while you
stay green: `uv run` re-locks silently where `uv sync --locked` refuses. In a
`uv run`-only session the sole local sign is a modified `uv.lock` in
`git status`.

`uv sync` prunes anything you did not ask for **on that command line**, lock
member or not, so name every extra at once (`uv sync --extra dev --extra fmpy
--extra om --extra author --locked`). CI installs `author` (NiceGUI), so the
authoring-shell tests run there; a local `uv sync --extra dev --locked` alone
leaves them skipped by their `importorskip`. A package inside `.venv` but outside the lock — `pytest-playwright`
for the browser tests — needs `--inexact` or the next sync removes it.
(`pre-commit` sidesteps this: install it with `uv tool install`, outside
`.venv`, as `.pre-commit-config.yaml` documents.)

A green local `pytest` does not predict CI, which also runs `ruff check`, `ruff
format --check`, `mypy`, and pytest under a 66% coverage floor with the
hardware-dependent markers deselected.

Run `mypy` bare, with no path. `[tool.mypy] files` in `pyproject.toml` scopes
it to the typed-dataclass tier (D88); passing a path — `mypy src` — overrides
that list and type-checks the out-of-scope modules too, reporting 140
pre-existing errors that are not yours and that CI never sees. `quality.yml`
and `.pre-commit-config.yaml` both invoke it bare.

## Configuration

The tool looks for `testing.json` near the library root or reference root. Key fields:

- `source_path` — path to the library's source location (Modelica package.mo directory, FMU directory, Julia project, ...; relative to testing.json)
- `source_type` — `"modelica"` (default) / `"fmu"` / `"julia"` / `"python"`; only `"modelica"` changes framework behavior (package.mo lookup + `.mo` recognizer scan), the others skip it and select recognizers by name
- `backend` — named entry like `"Dymola"` or `"OpenModelica"`; omit to auto-detect from the `backends` map
- `backends` — map of backend names to candidate executable paths
- `backend_setup` — list of Modelica commands run after library loading (user-specific settings)
- `dependencies` — library names (e.g. `"Modelica"`) or paths to dependency roots loaded before simulation
- `reference_root` — where reference results live (default: `<repo>/Resources/ReferenceResults`)
- `test_spec` — path to external test definitions file
- `recognizers` — custom JSON recognizers appended to the bundled list (see `discovery/json_recognizer.py`)
- `tolerance` — global NRMSE comparison tolerance (default: `1e-4`)
- `baseline_points` — the points a stored baseline keeps of its run: `"all"` (the default, every point the simulator wrote) or a whole number ≥ 2, which thins a longer run to at most that many; anything else is a config error. Scoring reads the run at the baseline's time points, so this is also the resolution of the comparison; the reference JSON records it under `sampling` (D153)
- `worker_startup_timeout` — seconds a persistent worker may take to start before the run fails with the backend's message, which names this key (default: 600 on every backend — the bound is for a tool that hangs; one that died fails at once)
- `max_embedded_samples` — LTTB decimation cap per variable in `interactive.html` (default: 1000)
- `diagnostic_variables` — variables auto-captured but not compared (default: `["CPUtime", "EventCounter"]`)
- `theme` — the bundled theme the dashboard and the report pages are generated in and `dstf author` opens in: `"default"` (the default) or `"dark"`; a name only, anything else is a config error (D148)

Note: `OutputCPUtime := true;` and `Advanced.UI.TranslationInCommandLog := true;` are hardcoded in the Dymola backend.

Reference results are partitioned by `<reference_root>/<Backend>/<os>/`. Soft-checks live under `.../soft_checks/ref_NNNN/<name>.json`, companions under `.../companions/ref_NNNN/<name>.{json,csv}` — a companion's metadata carries a `variables` mapping saying which of its variables draw on which of the test's.

### test_spec.json format

Simulation parameters live under `simulation`, comparison settings under `comparison`, optional explicit scoring tree under `metrics`, optional plot extras under `plots` (`[{"var": ..., "context": [...]}]` — the plots themselves are derived from the criteria, one per scored variable or array (a UnitTests element never folds), D140; an entry on a scored variable adds grey same-axis context series to its plot, one on an unscored variable is a watch plot; read by the report and the tool, ignored by scoring; any variable of the result file, tracked or not). Minimal entries need only `model` and `variables`. A criterion's tolerance is one number or a pair `{"lower": a, "upper": b}` (D147; `docs/usage.md` lists the fields that take one), while a base tolerance — `comparison.tolerance`, `testing.json`'s `tolerance`, `--tolerance` — is ONE finite number or its test is refused. A variable a `metrics` leaf names is tracked too: every backend records, stores and scores it after the `UnitTests` variables and the `variables` patterns (D142), with no change to `variables`. **Two-layer contract**: the model's `experiment(...)` annotation provides defaults; any field set in `simulation.*` overrides it. Recognized annotation fields: `StopTime`, `Tolerance`, `__Dymola_Algorithm` (→ `method`), `NumberOfIntervals`, `Interval` (→ `output_interval`). `StartTime` is **not** honored — every backend assumes `t=0`. Full contract details in `docs/usage.md`.

**A variable is named by its RESULT FILE** (D132). A `comparison.variable_overrides` key, a `metrics` leaf's `variable` and a `plots` entry's `var` and `context` all name that: for a variable of the Modelica `UnitTests` component it is `<instance>.x[i]` — `unitTests.x[1]` — not the text the model wrote in `x={...}`. That text is the variable's **expression**: recorded beside the name in the reference JSON, shown as the display label on the report and in `dstf author`, soft-checked on compare, and a key of nothing. A spec written before this names expressions; `dstf migrate-variable-names` rewrites those keys.

```json
{
  "tests": [
    {
      "model": "MyLib.Examples.SimpleTest",
      "variables": ["pipe.T[1]", "pipe.m_flow"],
      "simulation": {"stop_time": 100, "tolerance": 1e-6},
      "comparison": {
        "tolerance": 0.01,
        "variable_overrides": {
          "pipe.T[1]": {"tolerance": 0.1},
          "pipe.p[1]": {"mode": "tube", "tube_width_mode": "rel", "tube_rel": 0.02}
        }
      }
    }
  ]
}
```

### Comparison modes

- **`nrmse`** (default) — `RMSE / signal_range`; pass if below tolerance.
- **`tube`** — envelope around the reference trajectory. `tube_width_mode`: `"rel"` (fraction of |ref|), `"band"` (offset in signal units), `"abs"` (literal y-bounds, from control points). Supports time-varying control points with linear or stepwise interpolation.
- **`points`** — declared-checkpoint comparison. Empty list ⇒ final-value-only check (legacy behavior). Non-empty list ⇒ multi-point with per-point abs/rel y-tolerance and `time_tolerance` (x-tolerance) box check. Each point may set an explicit `value` (baseline-free) or fall back to `ref(time)`.
- **`range`** — scalar `min_value` / `max_value` bounds on the signal; baseline-free (bounds come from the spec).
- **`event-timing`** — compares event instants via Modelica duplicate-time detection; `time_tolerance` config.
- **`dominant-frequency`** — FFT peak matching against a declared-peaks table (`peaks: [{freq, tolerance, tolerance_mode}, ...]`); scored in Python like every mode — the report page draws the spectrum the scorer computed.
- **`scalar`** — scores one number named by `quantity`: a reduction of the leaf's variable (`{"reduce": "max"|"min"|"mean"|"sample_mean"|"final"|"integral"}`; `mean` is time-weighted, `sample_mean` arithmetic) or a dotted statistics / diagnostics-summary path (`"CPUtime.final"`). abs/rel `tolerance` against the baseline scalar, or baseline-free `min_value` / `max_value` bounds.

Every tolerance above but `nrmse`'s — and a tube's constant width (`tube_abs` / `tube_rel`), not its `tube_min_width` — is a number or a pair `{"lower": a, "upper": b}`: with `d = actual − target` a check passes iff `−lower ≤ d ≤ upper` (relative sides scale by |target|; in time, lower is earlier and upper later), so a spec of numbers scores as it always did (D147).

Leaves can be wrapped in an explicit `metrics` tree with combinators (`and`, `or`, `k-of-n`, `warn`, `weighted`) and scoped to a time window (`"window": {"start": ..., "end": ...}`).

### Tolerance resolution order

Per-variable override (spec) > per-variable override (reference JSON) > per-test comparison tolerance > reference JSON comparison tolerance > config.tolerance > default (1e-4). When accepting results, comparison settings are saved in the reference JSON's `comparison` section so tolerances travel with the baseline.

## Key Abstractions

- **`Config`** (`config.py`) — resolves all paths from CLI args + `testing.json` + defaults; passed to backends and reporters but not to comparison functions.
- **`TestModel`** (`discovery/test_registry.py`) — fully resolved test: model ID, simulation params, tracked variables, source, `simulate_only`, `requested_baselines`.
- **`Recognizer`** (`discovery/recognizer.py`) — registry of source-scan rules; bundled + user-declared JSON recognizers merged by `model_id`.
- **`unit_test_variables`** (`discovery/unit_tests.py`) — the ONE place a `UnitTests` variable is named: `<instance>.x[i]` (`TestModel.unit_tests_instance`, default `unitTests`) with the expression from `x_expressions` beside it. Every backend and the OpenModelica variable filter ask it; nothing spells `unitTests.x[` itself (D132).
- **`display_label`** / **`variable_texts`** (`backends/types.py`) — the one label rule and the one scope rule above it: what a person reads for a variable is `expression or name`, with the name beside it (`a.Q (unitTests.x[1])`) when another name in the TEST's scope — its tracked variables, its scored comparisons and every series of its plots — carries the same label. `reporting/context_builder.py::test_texts` builds that scope; both tiers call it (`Session.labels` is its text half). A surface that shows the muted name beside its title reads the plain label there; the console / JUnit / review keep `label_with_name` (no page, no scope). `VariableResult.expression` and `VariableComparison.expression` carry it; nothing is keyed by it.
- **`Backend`** (`backends/base.py`) — abstract interface; backends self-register via `@register` and declare `Capability` flags (`PERSISTENT_WORKERS`, `BATCH_FALLBACK`, `FMU_EXPORT`, `LIVE_RESULTS`, `CANCEL`). Concrete backends: `DymolaBackend` / `PersistentDymolaBackend`, `FmpyBackend`, `OpenModelicaBackend` / `PersistentOpenModelicaBackend`, `JuliaBackend` / `PersistentJuliaBackend`, `PythonBackend`.
- **`Worker`** + **`PersistentBackendBase`** (`backends/persistent.py`) — `Worker` ABC declares the long-lived-process contract every persistent backend must satisfy. It is itself a template-method class: `run_test_with_timeout` / `close` / `_kill_tracked_pids` (watchdog thread, timeout kill, process-tree kill incl. orphan snapshot, disk rescue, restart-accounting flags) are implemented once in the base. A backend supplies abstract `start` / `is_alive`, the `run_test` hook, and optionally `_evaluate_from_disk` / `_detach_interface` + `_shutdown_interface` / `_quiet` / the `kill_name_hint`, `timeout_kill_grace`, `exec_thread_prefix` class attrs. `PersistentBackendBase` is a template-method class owning the worker-pool orchestration (parallel startup, shared queue + sentinel dispatch, per-worker restart on death). New persistent backends supply a `Worker` subclass + a small set of hooks (`worker_cls`, `backend_label`, `make_worker`, `setup_before_workers`, `preflight`) — see [docs/extensibility.md](docs/extensibility.md) §3 "Persistent-worker contract".
- **`ReferenceStore`** (`storage/reference_store.py`; role CRUD in `storage/roles.py`) — primary refs + soft_checks + companions; `RefIndex` (`storage/ref_index.py`) built in-memory from scanning.
- **`ComparisonMode`** (`comparison/modes.py`) — strategy pattern for per-variable comparison with typed config dataclasses; field metadata (`label` / `help` / `ui_min` / `ui_max`) flows into auto-derived UI via `comparison/mode_schema.py`.
- **`MetricTree`** (`comparison/tree_spec.py` + `tree_eval.py` + `metric_tree.py`) — `LeafSpec` / `CombinatorSpec` + evaluator; `implicit_and_tree()` (`metric_tree.py`) for specs without an explicit tree; `validator.py` enforces baseline-role rules (≥ 1 primary leaf outside warn; soft_checks warn-wrapped; companions not targetable).
- **`comparator`** (`comparison/comparator.py`) — orchestrates per-test comparison; threads primary + named baselines into the evaluator; derives `TestComparison.passed` from the tree root.

## Design Principles

1. **Library-agnostic**: auto-detects library name from `package.mo` / project layout, all paths configurable.
2. **Backend-agnostic**: backend-specific code isolated under `backends/<name>/`; abstract interface with registry + capability flags.
3. **Stable test IDs**: numeric IDs (`ref_0001.json`) with model ID inside each file; IDs never reused; in-memory index built by scanning.
4. **Reference partitioning**: results split by backend and OS since solvers produce platform-specific results.
5. **Batch / persistent execution**: load libraries once per worker, run N tests, exit — avoids per-test startup overhead.
6. **No backward compatibility**: clean breaks during development; migration utilities provided for format changes.
7. **Strategy over conditionals**: modes, backends, recognizers, and UI renderers all use registry + strategy patterns, not if/elif dispatch.
8. **Economy of tools** (D66): the tool does regression testing and emits handoff-ready artifacts (JSON-Patch, JSON-Schema, baselines); calibration / RCA / parameter-estimation / ML-ranking belong in downstream tools that consume our outputs.
