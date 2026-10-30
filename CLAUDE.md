# CLAUDE.md

## Project Overview

**Dynamic Systems Testing Framework (DSTF)** — formerly **ModelicaTesting** — is a Python framework for regression and unit testing of time-dependent system behavior across simulated and pre-recorded trajectories.

**Current state**: a multi-backend regression harness. Discovers tests by scanning `.mo` files and/or reading `test_spec.json`; simulates via **Dymola** (Python interface default, batch `.mos` fallback), **FMPy** (prebuilt FMUs), **OpenModelica** (OMPython persistent-worker default, `omc` batch fallback), **Julia/ModelingToolkit** (persistent `julia` worker), or **Python** (subprocess-per-test; any `simulate(stop_time, tolerance) -> dict` — scipy, pandas, CSV, HTTP, etc.); scores via a composable **MetricTree** (leaves: `nrmse` / `tube` / `points` / `range` / `event-timing` / `dominant-frequency` / `scalar`; combinators: `and` / `or` / `k-of-n` / `warn` / `weighted`; multi-baseline via `"against"`; time-windowed leaves via `"window"`). Reports via ONE suite-wide HTML surface — the dashboard (also what `--report-format html --output` writes) — plus per-test **read-only Plotly HTML** (figures built in `dstf.figures` from the geometry each metric emits; server-rendered metric tree with the CLI's verdict, criterion and detail pairs; overlay picker and error overlays toggle traces already in the figure, the error overlays on two labelled error axes — the page computes nothing) + JUnit XML. Criteria are authored in `dstf author`, the Python-side tool over the same figure layer (D125): a Suite page and a Test page whose criteria are edited inline beside the plot they score (D131), where a tree node's forms open as a row under that node and a plot's legend holds its signals while each criterion's drawing is shown or hidden from its own row (D133), and a 40 px rail opens a Tests drawer (switch test) and a Variables drawer (D134) that adds context series to the plot you selected or makes a watch plot of one variable — the plots themselves are derived from the criteria tree, and `plots` holds only those extras (D140). References partitioned under `<reference_root>/<Backend>/<os>/` with three baseline roles: **primary** (regression anchor, hard-fail), **soft_checks** (warn-wrapped cross-regression imports), **companions** (plot-only overlays, including cross-library).

**Forward direction**: [docs/vision.md](docs/vision.md) lays out the six-layer plug-in architecture (Source → Discovery → Backend → Dataset → Metric → MetricTree). See [docs/architecture.md](docs/architecture.md) for the layer ↔ code mapping and its "Contracts inside the framework"; a plug-in author's side is `docs/guides/plugin.md` and the API reference.

**Project history**: [docs/decisions.md](docs/decisions.md) is the authoritative log (D44–D79 covers the work summarized above: Phase 1 abstractions, Phase 2 FMPy, Phase 3 MetricTree, Phase 4 multi-baseline + cross-backend + weighted, Phase 5 PTA recognizers, Phase 6 interactive reporter MVP + baseline-role split, D69–D70 OpenModelica, D77–D79 Julia/MTK, D80 Python-driven tests, D81 rename to DSTF, D125 the two-tier split, D131 the Test page, D132 variable identity, D133 the form row and legend, D134 the rail and drawers, D139 the Test page, D140 plots from the tree, D147 two-sided tolerance, D148 themes, D149 on-plot editing, D150 the simulation queue, D151 live results, D152 a criterion over an array family, D153 the baseline's points, D154 strict keys and the unscorable-criterion delta rule, D155 one act is one pending edit, D160 the command line as a tree, flags only where they apply, D162 diagnostic variables, one name one meaning). Do not re-summarize past phases in this file — grep `decisions.md` or `git log` when context is needed.

## Project Structure

```
DynamicSystemsTestingFramework/
├── src/dstf/                     # Python package (src layout)
│   ├── cli/                      # CLI package. parser.py: the command tree (build_arg_parser) — every command, the flags each reads
│   │                             #      and its help; main.py: the dispatch table. Commands: discover, run, compare, author,
│   │                             #      check (where each backend is on this machine, and whether the project's is ready), and the
│   │                             #      groups spec (add / update / schema), baseline (export), soft-check (add / list / remove),
│   │                             #      companion (add / list / freeze / remove), manifest (show / dump / cleanup)
│   ├── config.py                 # Config dataclass, path resolution, testing.json loading, which backend a project runs with
│   ├── config_shape.py           # testing.json's keys — type, default, description: the ONE table Config refuses an unknown key by,
│   │                             #      takes its defaults from and `spec schema testing` publishes; a backend's settings
│   │                             #      block is declared in the same shape (Setting) and checked by the same check
│   ├── json_types.py             # whether a JSON value is of the type its key takes, and how a refusal says it (both strict files)
│   ├── pipeline.py               # run_pipeline — the run as a library callable; cmd_run is its CLI wrapper
│   ├── discovery/                # Recognizer registry, JSON recognizer, test_spec parse (spec_parser: the ONE decoder of the spec file, which every command and the tool read it through), plots (a test's plots, derived from its criteria, + the `plots` extras key), RFC 6902 patch_apply,
│   │                             #      patch_envelope (both envelope forms), spec_update (the shared read-modify-write),
│   │                             #      unit_tests.py (the ONE place a UnitTests variable is named: <instance>.x[i] + its expression),
│   │                             #      scan_cache (the tool's scan cache under $XDG_CACHE_HOME/dstf/scan/),
│   │                             #      spec_shape.py (a spec entry's keys: the ONE table the parser refuses by and spec schema publishes),
│   │                             #      mo_parser.find_class (a class by name: a spec-only test's file and its experiment annotation),
│   │                             #      library_state.py (the source files' stamps at one Queue click: what a live worker compares before it reads the library again; and which file a path dependency is loaded by)
│   ├── backends/                 # Backend ABC + Dymola / FMPy / OpenModelica / Julia / Python backends
│   │                             #      (locate.py: where a backend's install is and by which rule it was found — `Backend.locate`,
│   │                             #      the ONE place an install is looked for; each backend declares its `settings` table)
│   │                             #      (openmodelica/init_xml.py: the system size omc writes to <model>_init.xml, read into the
│   │                             #      statistics key Dymola's translation log fills — translation.continuous_time_states)
│   │   ├── common/               # Shared: mat_reader, live_mat.py (a result file read while its run writes it), tree-kill, result variables, JSON-driver base, result_files (any variable of a result file),
│   │   │                         #      statistics.py (the zero-count rule: a system-size list's summaries, written and read in one place;
│   │   │                         #      and diagnostic_final: a listed name's final as the event-count warning, the dashboard's CPU time and the Statistics rows read it)
│   │   ├── pool.py               # SimulationPool: a FIFO queue of tests over a backend's runners that stays open (the tool's queue)
│   │   └── cross_backend.py      # EXPERIMENTAL Dymola→FMU→FMPy baseline chain
│   ├── comparison/               # ComparisonMode registry, MetricTree (metric_tree.py: the combinators) spec/eval, validator, tree_spec helpers,
│   │                             #      mode_schema (derive_schema — JSON-Schema export + the tool's forms),
│   │                             #      handles.py (edit_handle — a dragged handle back to a raw leaf),
│   │                             #      sides.py (Sides — the ONE place a tolerance's number-or-pair is read or written),
│   │                             #      snap.py (the one snap rule: a value the pointer placed is written at a readable step, D149),
│   │                             #      strict_keys.py (a key nothing reads is an error that names it — the ONE check, from each metric's config)
│   ├── storage/                  # ReferenceStore + roles mixin, RefIndex, Baseline, accept (the one accept path),
│   │                             #      baseline_codec (THE reader/writer of a baseline file; the rest sees storage.baseline types)
│   ├── authoring/                # Session, capability contract, spec document (its pending ops, counted by act: edit_counts, D155) — the tool's core;
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
│   └── reporting/                # status.py (the classifier + every status word and class, a refusal's reason, and failed_criteria — the one reading of a failing criterion the console's blocks and JUnit's lines are written from) + rows.py (the row model both HTML surfaces render: TestRow, COLUMNS)
│       │                         #      + tree_rows.py (a plot's criteria rows with their crumbs — the one walk both tiers list them by)
│       │                         #      + stats_rows.py (the Statistics rows both tiers render: the report's table and the card; one "changed")
│       │                         #      + console/junit + context_builder + report_suite + dashboard_render + overlay_loader (companion / soft-check overlays) + schema_export (`spec schema`) + theme.py (the token tables both tiers render, one per bundled theme: default and dark)
│       └── templates/            # interactive.html + axes.js + report.js (INTERACTIVE_JS_PARTS, copied out as interactive.js) + dashboard.* + filters.js (the typed column filters)
│                                 #      + vendor/plotly.min.js (offline-capable reports)
├── examples/
│   ├── modelica/ModelicaTestingLib/   # Demo Modelica library (UnitTests component + 14 tests: one per mode and combinator,
│   │                                  #      an array model — HeatRodTest, `T[20]` — MetricTreeTest with its `analytic` companion,
│   │                                  #      and ThermostatBuildingTest: four rooms scored by one 19-leaf tree that uses every metric and combinator)
│   ├── fmu/                           # Reference-FMUs demo (BouncingBall / Dahlquist / VanDerPol)
│   ├── julia/JuliaMtkTestingLib/      # Julia/MTK demo library (mirrors ModelicaTestingLib layout)
│   └── python/PythonTestingLib/       # Python demo library (SimpleRamp via scipy, ConstantCsv data loader)
├── tests/                        # pytest suite (~8700 tests, ~360 of them the Playwright subset, gated by importorskip)
├── docs/                         # the site: index.md, concepts.md, guides/, reference/ (generated by _ext/), img/; and contributor notes,
│                                 #      not on the site: decisions.md, vision.md, architecture.md, qa/, ideas.md, releasing.md
├── CHANGELOG.md                  # what changed per version, for the PyPI user (also a page of the site)
├── scripts/                      # fetch_reference_fmus.py, smoke_test_dymola_export.py
├── pyproject.toml                # uv/hatchling config; optional extras: [dev], [author], [browser], [fmpy], [om]
└── CLAUDE.md
```

## Running the Tool

The package ships a console script (`[project.scripts]` in `pyproject.toml`). The canonical dev invocation is `uv run dstf ...`; `python -m dstf ...` is the fallback (both call `cli.main_entry`). End users install `dynamic-systems-testing-framework` (`uv tool install` / `pipx install`) and run plain `dstf` — the distribution name differs from the import name because `dstf` on PyPI belongs to an unrelated project (D124). `docs/releasing.md` is how a version reaches PyPI.

```bash
uv run dstf --config examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json run --no-browser
uv run dstf run --help        # every command: what it does, what it writes, every flag, examples
uv run dstf check             # where each backend is on this machine
```

What each command does and every flag it reads is in the command tree itself (`cli/parser.py`, one table of what each command reads) and therefore in `dstf <command> --help` and on the site's generated reference (`docs/reference/cli.md`). The user's side — a first run, results, baselines, criteria, `dstf author`, CI, the simulators — is the guides under `docs/guides/`; build the site with `uv run sphinx-build -W --keep-going -b html docs docs/_build/html` (the `docs` extra). Every command line a page shows parses through the real tree (`tests/test_docs_commands.py`) and every JSON snippet is read by its file's loader (`tests/test_docs_snippets.py`).

The default work dir is `testing_output/<library>/<backend>/<os>/` under the CURRENT directory, and `--work-dir` goes after the command: run a scratch command from a scratch directory.

## Running Tests

```bash
uv sync --extra dev --locked       # One-time: build .venv from uv.lock (what CI runs)
uv run pytest                      # Run the suite
uv run pytest -m "not playwright"  # Skip browser-driven interactive-HTML tests
```

The suite is about 8,700 tests; CI's selection is about 8,250 of them. Add
`-n 4` to run it in four parallel workers (`pytest-xdist`, in the `dev`
extra), as CI does.

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
--extra om --extra author --extra browser --locked`). CI installs `author` (NiceGUI), so the
authoring-shell tests run there; a local `uv sync --extra dev --locked` alone
leaves them skipped by their `importorskip`. The browser tests (`-m
playwright`) need the `browser` extra (Playwright's own API; no pytest plugin)
and a Chromium: `uv sync --extra dev --extra author --extra browser --locked`,
then `uv run --no-sync playwright install chromium` once. CI runs them in a
job of their own (`browser` in `quality.yml`). A package inside `.venv` but
outside the lock needs `--inexact` or the next sync removes it (`pre-commit`
sidesteps this: install it with `uv tool install`, outside `.venv`, as
`.pre-commit-config.yaml` documents).

A green local `pytest` does not predict CI, which also runs `ruff check`, `ruff
format --check`, `mypy`, and pytest under a 66% coverage floor with the
hardware-dependent markers deselected.

Run `mypy` bare, with no path. `[tool.mypy] files` in `pyproject.toml` scopes
it to the typed-dataclass tier (D88); passing a path — `mypy src` — overrides
that list and type-checks the out-of-scope modules too, reporting 140
pre-existing errors that are not yours and that CI never sees. `quality.yml`
and `.pre-commit-config.yaml` both invoke it bare.

## Configuration

`testing.json`, `test_spec.json` and each backend's settings block are strict, and each is ONE table in the code that the loader refuses by, takes its defaults from, and the reference page and the published schemas render: `config_shape.TESTING_JSON` (and `RECOGNIZER`), `discovery/spec_shape.py` (an entry, `simulation`, `comparison`, an override, a criterion, a window, a group, a `plots` entry), each backend's `Backend.settings`, and each mode's config dataclass (its fields' `label` / `help` / `columns` metadata). A key's meaning, type and default live in its row's description and nowhere else: change the row, and `docs/reference/` follows (`tests/test_descriptions.py` holds every row to a description). Users read the generated pages (`docs/reference/testing-json.md`, `test-spec.md`, `comparison-modes.md`) and the guides.

Note: `OutputCPUtime := true;` and `Advanced.UI.TranslationInCommandLog := true;` are hardcoded in the Dymola backend.

## Key Abstractions

- **`Config`** (`config.py`) — resolves all paths from CLI args + `testing.json` + defaults; passed to backends and reporters but not to comparison functions.
- **`TestModel`** (`discovery/test_registry.py`) — fully resolved test: model ID, simulation params, tracked variables, source, `simulate_only` (a recognizer's mark; `is_simulate_only` is the rule a reader asks), `requested_baselines`.
- **`Recognizer`** (`discovery/recognizer.py`) — registry of source-scan rules; bundled + user-declared JSON recognizers merged by `model_id`.
- **`unit_test_variables`** (`discovery/unit_tests.py`) — the ONE place a `UnitTests` variable is named: `<instance>.x[i]` (`TestModel.unit_tests_instance`, default `unitTests`) with the expression from `x_expressions` beside it. Every backend and the OpenModelica variable filter ask it; nothing spells `unitTests.x[` itself (D132).
- **`diagnostic_names`** (`discovery/test_registry.py`) — the ONE place a test's listed names are composed: `testing.json`'s `diagnostic_variables`, then the entry's own, each once, in that order. Every backend records them as a result's `diagnostics` and the baseline stores each one's final / min / max; accept, the report and the tool ask it; and a scalar's final, min or max over the whole run on one is a card leaf by it (`tree_spec.on_the_card`): on the tool's Statistics card, in the report's metric tree and no plot (D162).
- **`display_label`** / **`variable_texts`** (`backends/types.py`) — the one label rule and the one scope rule above it: what a person reads for a variable is `expression or name`, with the name beside it (`a.Q (unitTests.x[1])`) when another name in the TEST's scope — its tracked variables, its scored comparisons and every series of its plots — carries the same label. `reporting/context_builder.py::test_texts` builds that scope; both tiers call it (`Session.labels` is its text half). A surface that shows the muted name beside its title reads the plain label there; the console / JUnit / review keep `label_with_name` (no page, no scope). `VariableResult.expression` and `VariableComparison.expression` carry it; nothing is keyed by it.
- **`Backend`** (`backends/base.py`) — abstract interface; backends self-register via `@register` and declare `Capability` flags (`PERSISTENT_WORKERS`, `BATCH_FALLBACK`, `FMU_EXPORT`, `LIVE_RESULTS`, `CANCEL`). Concrete backends: `DymolaBackend` / `PersistentDymolaBackend`, `FmpyBackend`, `OpenModelicaBackend` / `PersistentOpenModelicaBackend`, `JuliaBackend` / `PersistentJuliaBackend`, `PythonBackend`. Each also declares what it is configured by and where it is: `settings` (its block of `testing.json`'s `backends`, in `config_shape.Setting`s), `source_types`, `binaries`, `locate(settings)` (a `Located`: each part's path and the rule that found it) and `diagnose` (what `dstf check` prints) — `config.py` knows no backend, and a plug-in brings its own.
- **`Worker`** + **`PersistentBackendBase`** (`backends/persistent.py`) — `Worker` ABC declares the long-lived-process contract every persistent backend must satisfy. It is itself a template-method class: `run_test_with_timeout` / `close` / `_kill_tracked_pids` (watchdog thread, timeout kill, process-tree kill incl. orphan snapshot, disk rescue, restart-accounting flags) are implemented once in the base. A backend supplies abstract `start` / `is_alive`, the `run_test` hook, and optionally `_evaluate_from_disk` / `_detach_interface` + `_shutdown_interface` / `_quiet` / the `kill_name_hint`, `timeout_kill_grace`, `exec_thread_prefix` class attrs. `PersistentBackendBase` is a template-method class owning the worker-pool orchestration (parallel startup, shared queue + sentinel dispatch, per-worker restart on death). New persistent backends supply a `Worker` subclass + a small set of hooks (`worker_cls`, `backend_label`, `make_worker`, `setup_before_workers`, `preflight`) — see `docs/architecture.md` "Persistent-worker contract".
- **`ReferenceStore`** (`storage/reference_store.py`; role CRUD in `storage/roles.py`) — primary refs + soft_checks + companions; `RefIndex` (`storage/ref_index.py`) built in-memory from scanning.
- **`ComparisonMode`** (`comparison/modes.py`) — strategy pattern for per-variable comparison with typed config dataclasses; field metadata (`label` / `help` / `ui_min` / `ui_max`) flows into auto-derived UI via `comparison/mode_schema.py`.
- **`MetricTree`** (`comparison/tree_spec.py` + `tree_eval.py` + `metric_tree.py`) — `LeafSpec` / `CombinatorSpec` + evaluator; `implicit_and_tree()` (`metric_tree.py`) for specs without an explicit tree; `validator.py` enforces baseline-role rules (≥ 1 primary leaf outside warn; soft_checks warn-wrapped; companions not targetable).
- **`comparator`** (`comparison/comparator.py`) — orchestrates per-test comparison; threads primary + named baselines into the evaluator; derives `TestComparison.passed` from the tree root. `compare_one` is one test's comparison with its short-circuits (NOT RUN, SIM_FAIL, NO_REF) and its refusals — FAIL with a reason and no criterion (`reporting.status.refusal`): a base tolerance that is no number, a stored override nothing reads, a `metrics` tree that breaks a baseline-role rule (`broken_roles`: the validator's sentences — a soft check outside `warn` FAILED the test it may only warn on), and a test with no tree whose result holds none of the variables it tracks (`nothing_scored`: a test that scored nothing is not a PASS, and an accept skips it). A tracked variable the baseline holds and the run lost is a criterion not scored — `variable 'y' not found in the result` — on the flat path as under a tree, and the test fails; an accept over it stores the baseline and says which variables it `no longer holds`. A test that DECLARES nothing to score — no UnitTests variable, no `variables` pattern, no `metrics` tree and no `comparison.variable_overrides` entry; what `dstf spec add <model>` writes — is simulate-only (`TestModel.is_simulate_only`): it passes iff its simulation completes. An override on an entry that tracks nothing is a declaration that scores nothing: that test is refused too, with `variable_overrides key(s) name no variable this test tracks (x) — …`.

## Design Principles

1. **Library-agnostic**: auto-detects library name from `package.mo` / project layout, all paths configurable.
2. **Backend-agnostic**: backend-specific code isolated under `backends/<name>/`; abstract interface with registry + capability flags.
3. **Stable test IDs**: numeric IDs (`ref_0001.json`) with model ID inside each file; IDs never reused; in-memory index built by scanning.
4. **Reference partitioning**: results split by backend and OS since solvers produce platform-specific results.
5. **Batch / persistent execution**: load libraries once per worker, run N tests, exit — avoids per-test startup overhead.
6. **No backward compatibility**: clean breaks during development; migration utilities provided for format changes.
7. **Strategy over conditionals**: modes, backends, recognizers, and UI renderers all use registry + strategy patterns, not if/elif dispatch.
8. **Economy of tools** (D66): the tool does regression testing and emits handoff-ready artifacts (JSON-Patch, JSON-Schema, baselines); calibration / RCA / parameter-estimation / ML-ranking belong in downstream tools that consume our outputs.
