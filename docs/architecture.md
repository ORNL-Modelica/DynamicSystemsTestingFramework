# Architecture

> See [vision.md](vision.md) for where the framework is going. This document describes both the **forward conceptual model** (the six-layer abstraction every phase is converging on) and the **current state** (what is implemented today). Sections labelled *Current* describe today's code; sections labelled *Forward* describe abstractions we are aligning the code toward.

## Conceptual Model (Forward)

The framework is a pipeline of six typed, plug-in layers:

```
  Source          → what holds the behavior to test
    │               (Modelica library, FMU, Julia script, Simulink model, CSV / HDF5 data file)
    ▼
  Discovery       → how tests are produced from a Source
    │               (.mo UnitTests scan, test_spec.json, FMU directory scan, experiment registry)
    ▼
  Backend         → how a test is executed, with declared capabilities
    │               (Dymola Python / FMPy / OMPython / JuliaCall / MATLAB engine / data-file ingest)
    ▼
  Dataset         → typed result
    │               (TimeSeries, Scalars, Events, Spectrum, Distribution; Field reserved)
    ▼
  Metric          → scoring function on Dataset vs. baseline
    │               (NRMSE, tube, points, event-timing, spectral, Fréchet, KS, user-defined)
    ▼
  MetricTree      → composition: AND / OR / weighted / K-of-N → overall pass/fail + diagnostics
```

**Backend capabilities** are declared on the backend contract, not inferred from type:
- `capabilities: frozenset[Capability]` (`PERSISTENT_WORKERS`, `BATCH_FALLBACK`,
  `FMU_EXPORT`, `LIVE_RESULTS`, `CANCEL`) and `produced_datasets: frozenset[DatasetType]`.
Features (e.g. cross-backend verification, which chains `Backend.export_fmu()` → `FMPy.simulate()`) are composed from capabilities, not hardcoded modes.

### Layer ↔ Code mapping (Current)

| Layer | Current implementation | Status |
|---|---|---|
| Source | Four concrete `Config.source_type` values in the shipped demos: `"modelica"` (default — `config.source_path` → `package.mo` dir), `"fmu"`, `"julia"` and `"python"` (`config.source_path` → the FMU / project / script dir; per-test `"source"` field in spec). Only `"modelica"` is branched on in code (`config.py` package.mo lookup, `test_registry.discover_tests`); the rest select recognizers via `Recognizer.applies_to` and are otherwise opaque strings. | Simulink / data-file remain names only |
| Discovery | Pluggable `Recognizer` registry (`discovery/recognizer.py`). Bundled: `BundledModelicaUnitTestsRecognizer` (UnitTests + experiment) in `mo_parser.py`. User-provided: `JsonRecognizer` (`discovery/json_recognizer.py`) — declarative JSON in `testing.json`'s `"recognizers"` list, no Python. Plus `spec_parser.py` for `test_spec.json` (universal fallback). Merged by `model_id`. | Phase 5 / PTA: registry + bundled + JSON-driven all live; cross-source recognizers (FMU vendor-ext, Julia macros) unimplemented |
| Backend | `backends/` registry with five concrete backends: `DymolaBackend` / `OpenModelicaBackend` (native Modelica), `FmpyBackend` (FMU via FMPy), `JuliaBackend` (ModelingToolkit), and `PythonBackend` (arbitrary `simulate()`). Each declares `capabilities` + `produced_datasets`; Dymola/OpenModelica/Julia ship persistent-worker variants. `PersistentDymolaBackend` implements `export_fmu` via `translateModelFMU` (it needs a live `DymolaInterface`; the batch `DymolaBackend` has no export path). | Cross-backend chain (`backends/cross_backend.py`) wires Dymola export → FMPy simulate → named baseline. Validation caveat: export step requires Windows + Dymola FMI license (D63). |
| Dataset | Implicit `TimeSeries` returned by `read_result()`; `DatasetType` enum declared on backends | one concrete type materialized; additional types reserved for Phase 3 metrics |
| Metric | `comparison/modes.py`: `NrmseMode`, `TubeMode`, `PointsMode`, `RangeMode`, `EventTimingMode`, `DominantFrequencyMode`, `ScalarMode`; `VariableComparison.diagnostics` carries structured extras | 7 concrete leaf types spanning trajectory-comparison, signal-only bounds, event-timing, spectral shapes, and single-number (reduction / stored-statistic) checks |
| MetricTree | `comparison/metric_tree.py` (`AndCombinator`/`OrCombinator`/`KOfNCombinator`/`WarnCombinator`/`WeightedCombinator` + `MetricResult`), `comparison/tree_spec.py`, `comparison/tree_eval.py`. `compare_test` derives `passed` from the tree root; users author trees via `test_spec.json` `"metrics"` block | 5 combinators (weighted is direction-aware); `simulate_only` leaf short-circuits comparison entirely |
| Reference | Role-partitioned storage; `Baseline` view presents named baselines uniformly | primary baseline flat-at-top-level; soft_checks and companions in role subdirs beside it |
| (the run itself) | `pipeline.py`: `run_pipeline(config, tests, *, backend, store, merge, candidates)` — id mapping, stale-state wipe, live dashboard, `run_tests`, cross-backend chains, scope widening, result read. Prints the backend's narration unless `backend.quiet` is set; no exits, no browser, no prompts; failures are exceptions; no scoring (the commands score). Belongs to no layer — it orchestrates a backend, a store and a work dir — so it sits beside `config.py`. | The CLI drives it (`cmd_run`); the authoring tool's queue drives the same steps one test at a time (`ItemRun` behind `open_pool`, in the same module) |
| (the run's CLI) | `cli/run_cmds.py`: `cmd_run` is the wrapper — selection and filtering, `_get_backend` (the CLI's wrapper over `backends.select_backend`), `run_pipeline`, then the outcome's notices, `--accept` / `-i` / the report. `cmd_compare` shares the same helpers without simulating. | — |

Forward work: cross-backend verification beyond the Dymola→FMPy chain (each new primary backend needs its own `export_fmu`), and richer metrics (Fréchet, KS). The plug-in contracts are on the documentation site (`docs/guides/plugin.md`, the API reference) and in "Contracts inside the framework" below.

---

## Project Layout

```
DynamicSystemsTestingFramework/          # repo root (project name: DSTF)
├── examples/                            # Demo libraries — one per backend, used by README first-5-min walkthrough
│   ├── modelica/ModelicaTestingLib/       # Components/UnitTests.mo + Examples/ + Resources/ReferenceResults/
│   ├── fmu/                                # Reference-FMUs demo (BouncingBall / Dahlquist / VanDerPol)
│   ├── julia/JuliaMtkTestingLib/           # ModelingToolkit demo, mirrors the Modelica layout
│   └── python/PythonTestingLib/            # SimpleRamp (scipy) + ConstantCsv (pure data loader)
├── tests/                               # pytest suite (Playwright subset gated by importorskip)
│   ├── fixtures/results/Dymola/         # Real Dymola artifacts (.mat, dslog.txt, etc.)
│   └── test_*.py                        # Unit tests for all modules
```

## Package Layout

```
src/dstf/
├── cli/                      # argparse CLI package: discover, run, compare, author, check, and the groups
│   │                         #   spec (add / update / schema), baseline (export), soft-check (add / list / remove),
│   │                         #   companion (add / list / freeze / remove), manifest (show / dump / cleanup)
│   ├── parser.py             # build_arg_parser — the command tree: every command, the flags each reads, its help
│   ├── main.py               # the dispatch table (command path → handler) and the entry points
│   └── run_cmds.py           # cmd_run / cmd_compare — the CLI wrapper around pipeline.run_pipeline
├── config.py                 # Config dataclass, path resolution, testing.json loading, which backend a project runs with
├── config_shape.py           # testing.json's keys: type, default, description — the ONE table Config reads the file by
├── pipeline.py               # run_pipeline + RunOutcome — the run as a library callable (no printing, no exits)
├── discovery/
│   ├── recognizer.py         # Recognizer registry (bundled + user-declared, merged by model_id)
│   ├── mo_parser.py          # Scans .mo files for UnitTests components
│   ├── json_recognizer.py    # Declarative recognizers from testing.json
│   ├── spec_parser.py        # Parses test_spec.json (external test definitions)
│   ├── plots.py              # A test's plots, derived from its criteria (D140): plots_of_test — the ONE
│   │                         #   function both tiers call — runs derive_plots over plot_leaves (the
│   │                         #   tree's leaves, in order) and the `plots` extras (context series,
│   │                         #   watch plots); tracked_names, the plots of a test with no tree
│   ├── patch_apply.py        # RFC 6902 JSON-Patch round-trip (powers `dstf spec update`)
│   ├── patch_envelope.py     # ModelPatch + parse/build for the patch envelope (both forms)
│   ├── spec_update.py        # Read-modify-write of the spec: entry lookup, implicit-tree
│   │                         #   materialization, tree validation, apply_validated_patches
│   ├── unit_tests.py         # unit_test_variables — the ONE place a UnitTests variable is named
│   │                         #   (<instance>.x[i]) and paired with the expression the model wrote (D132)
│   ├── scan_cache.py         # The source scan cached under $XDG_CACHE_HOME/dstf/scan/ (the authoring tool);
│   │                         #   keyed by a DIGEST of the discovery modules' source, not a hand-bumped constant
│   └── test_registry.py      # TestModel dataclass, discover_tests() merges all sources
├── backends/
│   ├── __init__.py           # Backend registry: @register decorator, get_backend() factory
│   ├── base.py               # Backend ABC + RunSetup/_prepare_run (types/persistent/batch_manifest split out, D116)
│   ├── progress.py           # Backend-agnostic ProgressReporter (status.json + auto-refresh dashboard.html)
│   ├── persistent.py         # Worker ABC, WorkerRunner and PersistentBackendBase — the persistent-worker template
│   ├── pool.py               # SimulationPool: run_tests' dispatch kept open, a FIFO queue of tests (the tool's
│   │                         #   queue, D150)
│   ├── common/               # Shared: mat_reader (rows counted from the file's size), process tree-kill,
│   │                         #   result variables, JSON-driver base, statistics (the zero-count rule),
│   │                         #   result_files (any variable of a result file, by name: .mat / result.json / .npz),
│   │                         #   live_mat (LiveMatReader: a .mat polled while its run writes it)
│   ├── cross_backend.py      # EXPERIMENTAL Dymola → FMU → FMPy baseline chain
│   ├── dymola/               # DymolaBackend (.mos batch) + PersistentDymolaBackend (Python interface)
│   ├── fmpy/                 # FmpyBackend (in-process; per-test thread)
│   ├── openmodelica/         # OpenModelicaBackend (omc -s) + PersistentOpenModelicaBackend (OMPython ZMQ)
│   ├── julia/                # JuliaBackend (subprocess) + PersistentJuliaBackend (stdin-JSON pipe)
│   └── python/               # PythonBackend — subprocess-per-test, any `simulate(stop_time, tol) -> dict`
├── comparison/
│   ├── comparator.py         # compare_test / compare_all orchestration; per-mode scoring functions
│   ├── modes.py              # ComparisonMode ABC + 7 modes (nrmse, tube, points, range, event-timing,
│   │                         #   dominant-frequency, scalar); typed configs; resolve_mode()
│   ├── mode_schema.py        # derive_schema — per-mode schema from typed comparison configs
│   ├── handles.py            # edit_handle editors: a dragged handle back to a raw leaf (HandleEdit,
│   │                         #   HandleEditError); wired on MetricDescriptor beside summarize
│   ├── tree_spec.py          # MetricTree LeafSpec / CombinatorSpec + synthesize_implicit_tree() builder
│   ├── tree_eval.py          # Tree evaluator threading primary + named baselines
│   ├── metric_tree.py        # The combinators + MetricResult; implicit_and_tree() for a test with no tree
│   ├── registry.py           # MetricDescriptor / CombinatorDescriptor + register_metric / register_combinator
│   ├── sides.py              # Sides — the ONE place a tolerance's number-or-pair is read or written (D147)
│   ├── snap.py               # snapped — the one snap rule for a value the pointer placed (D149)
│   └── validator.py          # Baseline-role rules (≥ 1 primary leaf outside warn; soft_checks warn-wrapped)
├── storage/
│   ├── baseline.py           # The stored-baseline types: ReferenceFile, Baseline, StoredVariable, DiagnosticSummary, Sampling
│   ├── baseline_codec.py     # THE reader and writer of a baseline file (primary, soft check, JSON companion); number precision
│   ├── ref_index.py          # RefIndex + atomic text writes
│   ├── roles.py              # soft_check/companion CRUD mixin
│   ├── accept.py             # accept_run_results — the one accept path, with the aborted-run refusal
│   └── reference_store.py    # ReferenceStore: primary CRUD + accept path
├── authoring/
│   ├── session.py            # Session: open / rescore / view / stage / save / discard / export / apply
│   ├── capabilities.py       # The capability contract: Capability, Placement, Panel, the five outcomes
│   ├── spec_document.py      # SpecDocument — the spec as loaded, its pending ops and its on-disk hash
│   ├── tree_pointer.py       # node_at / node_or_none — a raw tree node by its /metrics pointer (UI-free)
│   ├── tree_text.py          # What a tree node reads as (leaf_name, node_text): the rows and Edit tree's
│   │                         #   form titles say the same thing (UI-free)
│   ├── family_rows.py        # A criterion over an array family as data (D152): which criteria rows fold,
│   │                         #   the elements an Indices text names, an edit of a fold as one whole-tree op;
│   │                         #   shell/family_fold.py draws the folded rows (UI-free)
│   ├── layout.py             # The plot extras' edits as pure functions: context series and watch plots,
│   │                         #   one op on /plots (D140)
│   ├── variable_rows.py      # The Variables drawer's rows as data; picker_rows.py, the add form's variable
│   │                         #   picker; suite_rows.py, a Suite row from the Session
│   ├── queue_rows.py         # The simulation queue as data: a row per item, the header's indicator (D150)
│   ├── live_rows.py          # A running test's page as data: reader snapshots to the series a plot draws, the
│   │                         #   words of each state; live_runs.py, the Session's readers of the running tests'
│   │                         #   result files and of unfinished runs' partial results (D151)
│   ├── situation.py          # A test's situation and the status strip's sentence
│   ├── shell/                # The NiceGUI tier: shell.py (the process: Session, open thread, event fan-out),
│   │                         #   suite.py + test_page.py (one object per browser tab; the one open editor
│   │                         #   or form row), tree_card.py (the criteria tree), plot_card.py (one plot,
│   │                         #   its criteria rows with their swatches, its error select and its editor),
│   │                         #   header.py, actions.py (ActionBar — a button per capability at a placement,
│   │                         #   its panel in a dialog; InlineHost — the same panels drawn inline, with
│   │                         #   a RowTarget to open a form row under their row),
│   │                         #   queue_panel.py (the simulation queue's panel and the header's indicator,
│   │                         #   D150), tabstate.py, figure_hook.py (one hook per plot),
│   │                         #   tab_theme.py (the tab's theme and its live switch, D148),
│   │                         #   suite_grid.py + suite_grid.js (the Suite table), strip.py (the Test page's
│   │                         #   status strip), stats_card.py, rail.py + test_list.py + variables_drawer.py
│   │                         #   (the two drawers), picker.py, family_fold.py, live_view.py (what a running
│   │                         #   test's drawn plots ask for, D151), placing.py (a field's ⌖ and the one armed
│   │                         #   field, D149), grips.js (a plot's tolerance arrows, D147)
│   ├── forms.py              # NiceGUI controls from a mode's derived schema
│   └── builtin/              # The capabilities that ship: edit_criteria.py, edit_family.py, edit_tree.py,
│                             #   accept.py (the shared accept function), enqueue.py (Queue: the selected
│                             #   tests handed to the Session's simulation queue),
│                             #   edit_on_plot.py (drag a handle or edit a list's table)
└── reporting/
    ├── console_report.py     # Terminal output with pass/fail, NRMSE, structural warnings
    ├── junit_report.py       # JUnit XML for CI
    ├── status.py             # Shared per-test status classifier (NOT RUN > SIM_FAIL > FAIL > NO_REF > PASS)
    │                         #   and every status word and class
    ├── rows.py               # The row model both HTML surfaces render: TestRow, COLUMNS
    ├── stats_rows.py         # The Statistics rows both tiers render: the report's table and the tool's card
    ├── dashboard_render.py   # The one suite-wide HTML page (live + final + --report-format html)
    ├── context_builder.py    # Per-test results → template context (the pure half)
    ├── report_suite.py       # Per-test interactive.html render + comparison_data.json sidecar
    ├── tree_rows.py          # A plot's criteria rows off a tree view (leaves, crumbs): the one walk
                              #   the report's sections and the tool's plot cards list criteria by
    ├── overlay_loader.py     # Companion CSV/JSON ingest + soft-check baseline projection
    ├── schema_export.py      # JSON-Schema emitter for test_spec.json (powers `dstf spec schema`)
    ├── theme.py              # The token tables, one per bundled theme (`default`, `dark`; D148) —
                              #   rendered to :root { --dstf-...: ... } for both pages and the tool,
                              #   and to the tool's Quasar palette; a dark theme's dimmed status parts
    └── templates/            # interactive.html (server-rendered tree, summary, banner) + axes.js
                              #   (the report's: a secondary axis visible while a trace on it is) + report.js
                              #   (both copied out as interactive.js via report_suite.INTERACTIVE_JS_PARTS)
                              #   + dashboard.html / dashboard.js + filters.js (the typed column filters)
                              #   + vendor/plotly.min.js
```

The figure layer sits beside them, and both tiers draw from it:

```
src/dstf/figures/
├── view.py                   # VariableView — the library-neutral picture of one variable
├── plotly.py                 # VariableView → a plotly figure dict (traces + shapes); editable=True
│                             #   (the tool's, never the report's) adds a draggable ring per handle;
│                             #   visibility=Visibility(hidden, error) (the tool's) keeps only signals in
│                             #   the legend, hides the leaves the page hid and shows one error series
│                             #   with its axis; the legend sits above the plot area on both tiers
│                             #   plot_figure: several views on one plot (families in a hue ramp);
│                             #   one view is variable_figure, unchanged
├── palette.py                # What a figure takes from a theme, by name (D148): its chrome, the
│                             #   eight series colours a dark plot lightens, the layout template
│                             #   (none for `default`: that figure is the one it always was)
├── families.py               # Array families: the last integer index, slice text, the hue ramp
├── assemble.py               # Trajectories + evaluated leaves → a VariableView
└── decimate.py               # LTTB decimation on the view, before any trace exists
```

`authoring/` is the authoring tier — the tool's state machine and the shell
`dstf author` serves. Only `shell/`, `forms.py` and the built-in panels import a
UI framework (`tests/test_authoring_imports.py` holds the line); the Session,
the capability contract and the spec document stay pure, so the rest of `dstf`
runs without the `[author]` extra. It shares the
CLI's paths rather than duplicating them: `discovery/spec_update.py` is the
read-modify-write both `dstf spec update` and `Session.save` call, and
`storage/accept.py` is the one accept path `run --accept`, `compare --accept`,
`run -i`'s `[a]` and the tool's Accept capability all call, and `pipeline.py` is
the run itself: `cmd_run` drives `run_pipeline`, and the tool's queue drives the
same path one test at a time (`open_pool` over `backends/pool.py`, D150).

On-plot editing crosses the layers without either importing the tool. The
figure layer draws the editable figure — `variable_figure(view, editable=True)`
turns on plotly.js shape dragging and adds one ring shape per handle marker of
the leaves `rings_for` names, each named with the handle's id — and
`Session.figure` is its one caller. The
comparison layer owns the inverse of each mode's handles in
`comparison/handles.py`, reached as `MetricDescriptor.edit_handle`, and each
list field's `columns` metadata in `modes.py`. The shell's figure hook — one per
plot card — turns a drag into a per-shape payload, and the Edit on plot
capability joins the two:
the handle's id names the leaf, `edit_handle` returns the new leaf, and the
Session stages one `replace` of it.

What a plot shows is chosen the same way, without the figure layer knowing the
page (D133). The tool's plot card keeps one choice per criterion — its row's
swatch — and the error series its select names, decides each criterion with
`drawn` (the open one always, then the swatch, then a failing one, then the
metric's `MetricDescriptor.drawn_by_default`), and hands the result to
`Session.plot_figure(…, visibility=Visibility(hidden, error))`. The figure
built with a `Visibility` sets `visible` on the hidden leaves' traces and
shapes, keeps them in place so shape indices do not move, and shows the chosen
error series with its axis; the choice is part of the card's redraw
signature, so a swatch click or a select change is one `Plotly.react`. A zoom
survives that redraw, except on an error axis given a new series: it scales
to that series afresh. The report's figure is built without one, keeps every
legend entry, and `templates/axes.js` shows an error axis while a trace on it
is visible and scales it afresh whenever the set of those traces changes.

## Data Flow

```
discover_tests(config)
    → mo_parser scans .mo files for UnitTests
    → spec_parser reads test_spec.json
    → test_registry merges both into list[TestModel]

backend.run_tests(tests)
    → creates ProgressReporter, registers each test (with report_dir from backend.ref_id_map)
    → generates startup.mos (loads libs, enables OutputCPUtime + TranslationInCommandLog)
    → generates per-test .mos (clearlog, simulateModel, savelog)
    → generates shutdown.mos, per-batch .mos scripts
    → splits tests into batches: the Dymola block's batch_size if set, else ceil(total/parallel)
    → submits all batches to ThreadPoolExecutor (queue-dispatched; worker_id from thread slot)
    → emits progress.on_start when batch begins, progress.on_finish per test as results parsed
    → parses dslog.txt (runtime stats) + translation_log.txt (structural stats) per test
    → progress.finalize() writes the last snapshot; dashboard.html stays live until the command renders the final page
    → returns list[BatchManifest] with TestRunResult per test

backend.read_results(manifests, tests)
    → lists variable names from .mat (fast header scan, no data loaded)
    → resolves variable patterns against available names
    → reads only needed variables from .mat via numpy.memmap (selective row access)
    → auto-captures diagnostic variables (configurable, default: CPUtime, EventCounter)
    → returns dict[model_id → TestResult]

compare_all(tests, results, store, default_tolerance, default_points)
    → loads reference JSON per test from ReferenceStore
    → per-variable: resolve_mode(override, tolerance, default_points) → ComparisonMode
    → mode.compare(ref_time, ref_values, act_time, act_values) → VariableComparison
    → NrmseMode: piecewise NRMSE with event boundary handling
    → TubeMode: envelope check with three width modes (rel, band, abs)
    → PointsMode: declared-checkpoint comparison (empty list ⇒ final value only;
                  non-empty ⇒ multi-point with abs/rel y-tolerance + x-tolerance box)
    → returns list[TestComparison]

reporters render TestComparison → console / JUnit / HTML / plots
    → context_builder turns results + comparisons into a context dict
    → report_suite writes the full-resolution comparison_data.json sidecar FIRST
    → figures/view builds one VariableView per variable from the trajectories and
      the evaluated leaves; figures/decimate thins it (LTTB) before any trace exists;
      figures/plotly turns it into a figure dict (traces + shapes for the criterion
      geometry each metric emitted: band, bounds, boxes, event windows, spectrum)
    → the trajectory arrays are dropped from the page context — interactive.html
      carries figure dicts and server-rendered markup only (tree, summary, pills,
      decimation banner); axes.js keeps a secondary axis visible exactly while a
      trace on it is; report.js paints the figures and toggles trace visibility
    → per-test report directories named ref_NNNN (has reference) or test_NNNN (no baseline)
    → the sidecar keeps its shape (full trajectory data) for downstream tooling
```

## Key Types

| Type | Location | Purpose |
|------|----------|---------|
| `Config` | `config.py` | All resolved paths, backend settings, tolerances |
| `TestModel` | `discovery/test_registry.py` | One test: model ID, sim params, tracked variables, source |
| `TestResult` | `backends/types.py` | Simulation output: success flag, variables, diagnostics, statistics |
| `VariableResult` | `backends/types.py` | One variable's time series (time + values arrays), its result-file `name` and its optional `expression` label (D132) |
| `TestComparison` | `comparison/types.py` | Comparison result: pass/fail, per-variable NRMSE, warnings |
| `ReferenceStore` | `storage/reference_store.py` | CRUD for reference JSON files via RefIndex |
| `RefIndex` | `storage/ref_index.py` | In-memory index mapping model IDs ↔ numeric IDs (built by scanning ref files) |
| `BatchManifest` | `storage/batch_manifest.py` | Maps test keys to `{"model_id", "ref_id", "last_run_status"}` within a batch run |

## Reference File Structure

`<reference_root>/<Backend>/<os>/ref_0001.json` — per backend+OS, self-contained.
Every baseline file — a primary, a soft check, a JSON companion's data — is
read and written by ONE module, `storage/baseline_codec.py`; the rest of the
code sees the `storage/baseline.py` types (`ReferenceFile`, `Baseline`,
`StoredVariable`, `DiagnosticSummary`, `Sampling`), never the parsed dict.
Decode -> encode of an unchanged file is byte-identical. The keys, in the
order the codec writes them (optional ones only when present):
```json
{
  "model_id": "...", "test_id": "0001",
  "status": "active",
  "date_added": "2026-01-15T...", "last_updated": "2026-04-08T...",
  "simulation": {"stop_time": 100, "tolerance": 1e-4, "method": "Dassl", "number_of_intervals": 500, "output_interval": null},
  "sampling": {"setting": "all", "source_points": 501, "stored_points": 501, "precision": "single"},
  "provenance": {"backend": "Dymola", "backend_name": "Dymola 2026x", "tool_version": "Dymola Version 2026x, 2025-10-10",
                 "library_versions": {"Modelica": "4.1.0"}, "dstf_version": "1.2.0", "os": "linux",
                 "run_started": "2026-10-15T09:12:44+00:00"},
  "comparison": {"tolerance": 0.01, "variable_overrides": {"pipe.T[1]": {"tolerance": 0.1}}},
  "statistics": {
    "translation": {
      "continuous_time_states": 4,
      "nonlinear": [3, 1], "nonlinear_count": 2, "nonlinear_total": 4, "nonlinear_max": 3,
      "init_nonlinear": [5], "init_nonlinear_count": 1, ...
    },
    "simulation": {"cpu_time_integration": 0.5, ...},
    "CPUtime": 12.3, "EventCounter": 42
  },
  "diagnostics": [
    {"name": "CPUtime", "final": 12.3, "min": 0.0, "max": 12.3},
    {"name": "EventCounter", "final": 42.0, "min": 0.0, "max": 42.0}
  ],
  "n_vars": 3,
  "time": [0.0, 0.1, ...],
  "variables": [{"index": 1, "name": "pipe.T[1]", "expression": "", "values": [...]}]
}
```

`sampling` says what accept did to the run's rows (D153) and the precision
the arrays are read back through: a `"single"` file's numbers are written with
9 significant digits and read back through float32, a `"double"` file's with
17 — the run's own numbers either way. A diagnostic is stored as a summary
(`final`, `min`, `max`), never a trajectory; an old `{name, values}` entry is
summarized by the codec on read.

`provenance` is written by every accept (`storage/accept.py::_provenance`):
the `RunMetadata` of the run that produced the accepted result — read from the
`run_stats.json` that `BatchManifest.record_result` writes beside each result,
never from `status.json`, which is one snapshot per work dir — with
`generated_at` as ISO-8601 `run_started`: the start of the run that produced
the result, which for a test run from the tool's queue (`pipeline.ItemRun`,
one test per run) is that test's own start (`QueueItem.started_at`). No
machine name, no load. A run that
recorded no metadata still gets the whole block: `backend`, `backend_name` and
`os` from the config, the rest `null`. A file accepted before the block
existed has none until its next accept.

A variable entry is `{"index", "name", "expression", "values"}` in that order
(D132). `name` is the name the RESULT FILE gives the variable —
`unitTests.x[1]` for a variable of the Modelica `UnitTests` component — and is
what every key addresses. `expression` is the text the model wrote for it in
`x={...}`, a display label and never a key; it is always written, `""`
included, so an entry without the field is one no writer of this version made.
A compare where both sides record an expression and they differ raises a
structural warning, not a failure.

The `comparison` section is optional. When present, it stores the comparison tolerances that were active when the baseline was accepted, so tolerances travel with the reference data. `variable_overrides` maps variable names to per-variable settings including `tolerance` and optional tube comparison parameters (`mode`, `tube_width_mode`, `tube_abs`/`tube_rel`, `tube_points`, `tube_interpolation`).

No persistent manifest file for the index. The in-memory `RefIndex` is built by scanning ref files at startup.
Valid statuses: `active` (normal), `skip` (temporarily excluded), `obsolete` (pending deletion).

### Multiple named baselines (D66)

A reference file carries exactly one baseline — the ``"primary"``, stored as
flat top-level fields. Additional named baselines are separate files in the
role subdirs beside it: ``soft_checks/ref_NNNN/<name>.json`` (scorable,
warn-wrapped) and ``companions/ref_NNNN/<name>.{json,csv}`` (plot-only).

```json
{
  "model_id": "...", "test_id": "0001", "status": "active",
  "date_added": "2026-01-15T12:34:56Z",
  "last_updated": "2026-02-20T10:00:00Z",

  // --- primary baseline (flat top-level fields; authoritative) ---
  "simulation": {"stop_time": 100, "tolerance": 1e-4, ...},
  "comparison": {"tolerance": 0.01, "variable_overrides": {...}},
  "statistics": {...},
  "diagnostics": [...],
  "n_vars": 3,
  "time": [...],
  "variables": [...]
}
```

A soft_check file holds the same per-baseline fields plus ``provenance``, in
the order the codec writes them (``time``, ``variables``, then
``diagnostics``, ``simulation``, ``statistics`` and ``provenance`` when
present):

```json
{
  "time": [...], "variables": [...],
  "provenance": {
    "origin": "rig-run-2024-03-15",
    "captured_at": "2024-03-15T09:20:00Z",
    "notes": "Benchmark 3B, lab 2 test stand.",
    "citation": "Internal report XYZ-2024-042, §4.3"
  }
}
```

Readers see ``Baseline``s: ``ReferenceStore.get_reference(model_id)`` is the
file's ``ReferenceFile``, whose ``baseline`` is the ``primary``;
``get_soft_checks(model_id)`` gives every soft_check, and the comparator scores
a tree against the two together. Companions are excluded — they cannot be
scored against.

Separate files mean accepting fresh primary results can never clobber a named
baseline. A top-level ``baselines`` key left in an old ref file is not read
and is dropped on the next accept.

The three baseline roles serve three workflows:

* **Regression** — the primary baseline alone (the default).
* **Validation against experiment** — primary + an `experiment` soft_check; a
  leaf referencing ``"against": "experiment"`` gates or warns on that
  comparison.
* **Cross-backend verification** — baselines from several backends
  registered as soft_checks under different names, each targetable
  by its own leaf.

See ``src/dstf/storage/baseline.py`` for the types, ``baseline_codec.py`` for
the file format, and
``src/dstf/storage/roles.py`` for the role CRUD. The comparator threads soft_checks into the tree evaluator and
the reporter renders per-leaf `against=` provenance — both shipped in Phase 4 /
D66.

Two manifest files are written to the work directory before simulation starts:
- `batch_manifest.json` — maps `test_key -> {"model_id": "...", "ref_id": "ref_NNNN", "last_run_status": "..."}` for all tests in the batch
- `reference_manifest.json` — maps ref IDs to model names; also available via `manifest dump` CLI command

## Backend Abstraction

### Current (as of D80)

```
Backend (ABC)
  ├── capabilities: frozenset[Capability]           # PERSISTENT_WORKERS, BATCH_FALLBACK, FMU_EXPORT, LIVE_RESULTS, CANCEL
  ├── artifact_files: tuple[tuple[str, str], ...]  # (filename, label) pairs for the report
  ├── run_tests(tests) → list[BatchManifest]       # override for batch execution
  ├── run_single_test(test, ...) → TestRunResult   # override for per-process execution
  ├── read_result(test, ...) → TestResult          # abstract — must implement
  └── read_results(manifests, tests) → dict        # shared implementation
        │
        ├── DymolaBackend (backends/dymola/)
        │     ├── Python interface (default) — persistent workers, live phase labels,
        │     │     per-test timeout watchdog with disk-check rescue, worker restart
        │     ├── Batch fallback (--batch) — .mos scripts, parallel workers, psutil kill
        │     └── read_result() — mat_reader + variable pattern resolution
        │
        ├── FmpyBackend (backends/fmpy/)
        │     ├── fmpy.simulate_fmu on the prebuilt FMU, writes npz result
        │     ├── concurrent.futures timeout per test
        │     └── phase labels: loading / simulating
        │
        ├── OpenModelicaBackend (backends/openmodelica/)
        │     ├── Persistent workers (the default; absent OMPython aborts)
        │     │     — OMCSessionZMQ per worker, library-load amortized across tests
        │     │     — psutil kill on timeout, disk-fallback, 3× worker restart
        │     ├── Batch fallback (--batch) — .mos + omc subprocess per test
        │     └── read_result() — mat_reader (DSresult-compatible)
        │
        ├── JuliaBackend (backends/julia/)         ← D77/D78/D79
        │     ├── Persistent workers (default)        ← D78
        │     │     — long-lived julia --project=... subprocess reading JSON-per-line
        │     │       requests from stdin, writes JSON responses to stdout
        │     │     — `using MTK, OrdinaryDiffEq, JSON3` paid ONCE per worker
        │     │     — ready-pulse handshake front-loads warmup cost
        │     │     — Base.invokelatest(build_mtk_system) after include for fresh gen
        │     │     — psutil kill on timeout, 3× worker restart
        │     ├── Batch fallback (--batch) — `julia run_test.jl ...` per test
        │     └── read_result() — JSON parser; materializes unknowns + observables
        │
        └── PythonBackend (backends/python/)       ← D80
              ├── Subprocess per test only (no persistent worker yet) — shipped
              │     driver imports the user's .py and calls
              │     simulate(stop_time, tolerance) -> dict
              ├── Shares backends/common/json_driver.py with JuliaBackend
              └── read_result() — same JSON parser
```

Each backend declares its capabilities via `frozenset[Capability]`:

- `PERSISTENT_WORKERS` — long-lived worker process; library loads once, amortized across N tests.
- `BATCH_FALLBACK` — per-test subprocess; robust but slower per-test startup cost.
- `FMU_EXPORT` — can export an FMU from the source (`PersistentDymolaBackend` only today — the batch `DymolaBackend` has no `.mos` export path and does not declare it; OM deferred per D69).
- `CANCEL` — can stop a test that is running, from another thread: the simulation queue's Cancel (the three persistent variants and `PythonBackend`; see "The simulation pool" below).
- `LIVE_RESULTS` — the result file grows while the run is in progress; `live_result_path(test_dir)` names it and `backends/common/live_mat.py`'s `LiveMatReader` follows it (Dymola and OpenModelica; see "Live results" below).

`backends.select_backend(config, persistent=True)` — which `cli/run_cmds.py`'s `_get_backend` wraps for the CLI — swaps to the persistent variant only when the backend **declares** `PERSISTENT_WORKERS`; a backend without the flag takes the batch path. A persistent preflight that raises `RuntimeError` aborts with a message pointing at `--batch` (no silent fallback). The cross-backend chain gates the same way on `FMU_EXPORT`.

### Forward

Each Backend declares capabilities; the framework enables/disables workflows based on what the Backend supports rather than on its class.

```
Backend (ABC)
  ├── capabilities: frozenset[Capability]           # PERSISTENT_WORKERS, BATCH_FALLBACK,
  │                                                  # FMU_EXPORT, ...
  ├── produced_datasets: frozenset[DatasetType]    # {TimeSeries, Events}, or {TimeSeries} for data-file ingest
  ├── run_tests(tests) → list[BatchManifest]       # orchestration (persistent workers / batch / ingest)
  ├── read_result(test) → Dataset                  # typed result (not only TimeSeries)
  └── export_fmu(test) → Path                      # optional; present iff FMU_EXPORT is declared
```

Concrete targets (roadmap): `DymolaBackend` (implemented), `FmpyBackend` (Phase 2 — implemented), `OpenModelicaBackend` (D69–D70 — implemented), `JuliaBackend` (D77–D79 — implemented; Dyad untested but should work via same path), `PythonBackend` (D80 — implemented), `MatlabBackend` (future), `DataFileBackend` (experiments — future).

---

## Contracts inside the framework

What a contributor needs to change the parts below without breaking what
leans on them. The user's and the plug-in author's side of the same
interfaces is on the documentation site (`docs/guides/`, `docs/reference/`)
and in the docstrings the API page renders.

### The `.mat` reader: rows are counted from the file's size

`backends/common/mat_reader.py` is the one reader of DSresult `.mat` files
(Dymola's `dsres.mat`, OpenModelica's `result_res.mat`). Its rule:

- **How many time points a file holds comes from its size, never from the
  count in its header.** The trajectory matrix `data_2` is the file's last
  matrix and both tools write its header before its rows. Dymola's count
  starts at 1 and stays behind the rows while a run is in progress;
  OpenModelica's is 0 until the run ends (unless `-mat_sync=1`, below).
  Neither is completed when a run is killed. `read_result_mat` and
  `list_result_mat_variables` therefore read the whole time points present:
  a file cut mid-point yields the points before the cut, and a short file
  never raises.
- **Readable is not finished.** A verdict still asks for a finished file,
  and what says so is the header's own count: it equals the points present,
  there is at least one, and no byte follows them. `read_mat_time_extents`
  and `mat_reaches_time` (the Dymola completion check, the OpenModelica disk
  rescue) answer for a finished file only. `mat_shortfall(path)` says in
  words why a file is not one; both DSresult backends' `read_result` ask it
  (`result_variables.unfinished_result`), so the rows of a killed run come
  back to be **shown**, inside a result that is a failure — nothing scores
  them or offers them as a baseline.

A backend that writes this format reuses the reader and the gate; one that
writes another format owes the same two properties to its own reader if it
wants its partial results shown.

### Live results: reading a result while its run writes it

A backend whose result file grows during a run declares
`Capability.LIVE_RESULTS` and overrides one classmethod:

```python
@classmethod
def live_result_path(cls, test_dir: Path) -> Path:   # test_dir = work_dir/<test_key>
    return test_dir / "dsres.mat"
```

The base class returns `None`. Registration fails for a class that declares
the flag without the override. Dymola and OpenModelica declare it (batch and
persistent classes); Python, Julia and FMPy write their result once, at the
end, and do not.

The file is followed with `backends/common/live_mat.py`:

```python
reader = LiveMatReader(path, max_rows=2000)   # one per result-file path; no I/O yet
snap = reader.poll(names)                     # about once a second
```

`poll(names)` returns an immutable `LiveSnapshot`:

| Field | Meaning |
|---|---|
| `state` | `"gone"` — no file at the path (not created yet, or removed); `"starting"` — a file whose header matrices are not whole yet; `"running"` — rows are served as they arrive. A finished file also reads `"running"`: the reader does not judge whether a run has ended. |
| `run` | Which file under the path this is, from 0. It goes up each time the reader starts over; a caller that accumulates series drops them when it changes. |
| `rows` | Whole rows (time points) the file holds. |
| `time` | The time of the last whole row; `None` before the first. |
| `series` | `{name: LiveSeries(time, values)}` — for each requested name the file has, the samples that are **new since that name was last served** (empty arrays when none are; a name the file lacks is left out). Read-only arrays in the file's own precision, resolved by the full reader's code: an alias keeps its sign, a parameter is a constant. |

What the reader guarantees, and why:

- **Nothing is held between polls**: open, `fstat`, read, close. A
  persistent worker wipes a test's directory before each run, and on Windows
  a held handle makes that removal fail.
- **No memory map**: a map over a file that is then truncated in place kills
  the process.
- **Only new rows are read**: in the steady state one read, from the last
  row served through the newest. A read of rows is bounded at 16 MB.
- **It never raises** for what the file is: short, half-written, vanishing,
  being recreated, or not a result at all.
- **A new run is recognised** when the file was gone at a poll, its
  (device, inode) changed, it got smaller, or the last row served no longer
  reads back as it did (a recreated file takes the removed one's inode on
  ext4, so identity and size alone can say "the same file, longer") — or,
  while no row was served yet, its header names another structure than the
  one read before (other names, other columns, another row shape). Not
  recognised: a file put in place of one whose rows were served, no smaller
  than it, with the same bytes at the last row served.
- **A poll returns at most `max_rows` samples per name.** A longer backlog —
  the first poll of a run long under way, a name first asked for late — is
  thinned to that many, evenly spaced and always ending on the newest row.
  Thinned rows are not served later; the finished file is read by
  `read_result_mat`.
- A name's samples, concatenated over the polls of one run, are that name's
  series, whenever it was first asked for.

A reader belongs to one poller; it is not thread-safe.

**`Config.live_results`** (default off) is the one run option: a caller that
will watch a run sets it before building the backend. There is no
`testing.json` key and no CLI flag, so `dstf run` is never changed by it. A
backend whose file would otherwise reach the disk late pays to write it
promptly: OpenModelica adds `simflags="-mat_sync=1"` to its `simulate()`
call (without it rows arrive a 4 KB block at a time). Dymola appends as it
goes and needs nothing. Under `-mat_sync=1` OpenModelica also keeps the
header's count current, so a watched run's file is "finished" by its header
at every moment; what stops a killed one from passing is that it stopped
short of the stop time, and the run's recorded verdict.

**What `dstf author` does with it** (D151). The tool sets
`Config.live_results` for the pool it opens. While a test runs on a backend
that declares `LIVE_RESULTS`, its Session keeps one `LiveMatReader` on
`live_result_path(work_dir/<test_key>)` — built once the item has its key,
which is after the run's directory was cleared — and polls it once a second
for the variables the open pages' drawn plots show. What a plot can be
made of is listed by that reader too (`LiveMatReader.names`, once the
file's header is whole): `result_names(test_dir)` is asked of a finished
result only. After a run that did not finish, the same path is read by
the partial read (`mat_reader.ResultMatFile`) to show what it wrote and
to list it. A
backend with a growing file in another format gets none of this until the
contract grows a reader of its own.
### The `VariableResult` contract: name vs expression

Each `VariableResult` a backend produces carries two texts (D132):

- **`name`** — the name the RESULT FILE gives the variable. This is the
  identity: overrides, metric-tree leaves, `plots` series, baseline entries,
  figure `meta.id`s, `data-variable` attributes and test markers are all
  keyed by it. A backend never invents one and never renames a variable to
  something more readable; a nameless `VariableResult` is a bug, and no
  consumer falls back to the index.
- **`expression`** (optional, `""` by default) — the text the model wrote for
  that variable, when the source has such a text and the result file's name
  does not carry it. Only the Modelica `UnitTests` path has one today: the
  result file names those variables positionally (`unitTests.x[3]`), and the
  expression is what `x={...}` said. FMPy, Julia and Python results carry
  result-file names already, so their `expression` stays `""`.

`expression` is a LABEL and is never used as a key.
`backends/types.py::display_label(name, expression)` is the one rule for what a person reads
(`expression or name`), and `variable_texts` is the scope rule above it: over
every name a TEST's page can show — tracked variables, scored comparisons and
plot series — it adds the name beside a label another of them also carries
(`a.Q (unitTests.x[1])`). A title that already shows the muted name reads the
plain label; every other surface reads the disambiguated text.
Producing it is `discovery/unit_tests.py::unit_test_variables` — the one
place a `UnitTests` variable is named — not each backend's own string
formatting.

**The one exception** is overlay matching, in
`reporting/overlay_loader.py::attach_overlays_to_trajectories`. An overlay is
another source's data — a companion is a different library's or a different
tool's run — and it names the quantity the way ITS result file does, so a
positional `unitTests.x[3]` is a name no other source can be expected to use.
Which of a companion's variables draws on which of the test's is therefore
recorded in the companion's own metadata, as the `variables` mapping
`dstf companion add --map` writes; where a companion has one, it is the whole
rule, and a trajectory it does not name draws nothing from that companion.
Only a COMPANION with no mapping falls back on spellings — the ones registered
before there was a mapping to write. It tries, in order, the companion variable
whose name is the trajectory's name, whose name is its expression, and whose own
expression is either. A fallback step that finds two candidates draws nothing and
warns: `x={a.Q, a.Q}` is legal, so one expression can belong to two of a
companion's variables, and guessing puts somebody else's curve under the wrong
plot. Nothing else falls back: a soft_check, a cross-backend import stored as
one, and an auto-discovered sibling-backend reference are DSTF's own data keyed
by the names this test uses, so they match BY NAME ONLY — and a soft_check is
also SCORED by name (`against:`), so the curve drawn for it has to be the curve
scored for it. A companion whose recorded mapping could not be read (a
hand-edited `variables`) keeps its record's authority too: it says what was
wrong on the overlay table and draws only what survived reading it.

### Persistent-worker contract

If your backend can hold a loaded model in memory across tests (declares `Capability.PERSISTENT_WORKERS`), inherit from `PersistentBackendBase` (in `backends/persistent.py`) and supply a `Worker` subclass — you do **not** re-implement the dispatch loop. The template owns the orchestration; you fill in backend-specific construction and lifecycle hooks.

**`Worker` ABC** (in `backends/persistent.py`) declares the long-lived-process contract every persistent backend's worker must satisfy. Two of its methods — `run_test_with_timeout` and `close` — are **template methods** implemented once in the base: the watchdog thread, the timeout kill, the process-tree kill, the disk rescue and the restart-accounting flags were identical in every backend. You supply hooks, not a watchdog.

What the base guarantees, for every worker: `run_test` executes on a daemon exec thread joined with the test's timeout; on expiry the worker is `close()`d (graceful attempt on its own daemon thread, abandoned after `timeout_kill_grace`, then `kill_process_tree` on every tracked PID and on the children recorded by the tree walks `close()` takes before the graceful attempt and every 0.25 s during it — a child that first appears *inside* that window, as dymosim does when the timeout lands during translation, is only reachable while its parent is still alive); `_evaluate_from_disk` then gets a chance to rescue a simulation that finished just before the kill. Timeout results carry `worker_killed=True` (a *healthy* worker died because the test was slow — the dispatch loop must not charge it against `max_restarts_per_worker`); worker exceptions leave it `False` (a genuinely broken worker DOES cost budget). `self.pids` and `self._n_restarts` are declared by `Worker.__init__`, so `super().__init__(worker_id, config)` is not optional.

Three more things the base does for every worker, each with a hook for what only the backend knows:

- **A bounded start.** Nothing calls `start()` bare: `start_within()` runs it on a daemon thread against `startup_bound()` — `worker_startup_timeout` from `testing.json`, else the class's `startup_timeout` (600 s on every backend: the bound is for a tool that hangs, and a large library is minutes of `loadFile` in a live omc) — and raises `WorkerStartError` at once when the tracked process is gone while `start()` still waits on it. A start that ran out says so with the step and the key: `did not start within 600 s — omc is still running during loadFile("...") (raise "worker_startup_timeout" in testing.json if this library needs longer)`. While a start is in flight `Worker.starting()` returns `(self._step, seconds so far)`; the simulation pool lists those as `SimulationPool.starting`. OMPython waits for omc's reply with no bound, so an omc that died during `loadModel` used to hold a run for good, every test "queued". The reason is in the backend's words: what `start()` raised, or `process_detail()` (exit code or "still running", the step in `self._step`, the log's tail). A start given up on is closed again if it ever returns, and that worker object is never started again. When no worker starts, `run_tests` fails every test with `No <label> worker could start — worker 0: <reason>; ...` and raises the same sentence.
- **A death that takes its children with it.** While a test runs the watchdog looks at the tracked process every `_CHILD_WATCH_SECONDS` (0.5 s) and keeps its live children on record. A tool that dies by itself fails the test (`Worker process exited during the test`), and `close()` kills the recorded children — an orphaned dymosim is out of reach of a tree walk from a parent that no longer exists. A child born after the last look is on no record; on Linux it is found all the same, by a mark of three parts that must all hold (`proc_kill.orphans_in`): it has no parent of its own left, it was born after the worker's tool, and it runs in the directory of the test that worker was running (`work_dir/<test_key>` — so a backend's `run_test` should have its tool work there). Out of reach: such a child that left the test's directory, and on other platforms any child born after the last look. A tool that died while `run_test` still returned is closed too, so the next test restarts it. Charged against the restart budget like any worker-side malfunction. A tool that died while its worker sat IDLE — between two tests of a run, or in a queue with nothing waiting — fails no test: the dispatch step (`WorkerRunner`) asks `is_alive()` before every test, and a worker whose `tool_gone()` is closed and restarted (a bounded start like any other, listed in `SimulationPool.starting`, charged to the budget) before the test runs. A test is failed for a dead tool only when it died during that test, and `self._step` is cleared as each test ends, so the message never says "during" an earlier test's call.
- **A cancel.** `request_cancel(test_key)` (any thread) ends the watchdog's wait with a `TestRunResult(cancelled=True)`: after `cancel_running(test_key)` and the tool's own return when that hook has a stop for the simulation alone — the worker is reused — else after the kill a timeout uses, uncharged. A run that finished anyway keeps its result. Dymola's hook writes a file named `stop` into the directory of the process NAMED dymosim (never matched in a command line: the compiler is handed a `dymosim` argument too) and has none during translation; OpenModelica's kills omc's children. A worker that overrides the watchdog (Julia) reads `self._cancel` in its own loop. Declare `Capability.CANCEL` on the backend class once a worker hears cancels.

`is_alive()` answers for two things: the handle (`_detach_interface` clears it, and `close()` is the only thing that calls that) and the tool's process (`Worker.tool_gone()`: the tracked PID exited, or is no longer a process of `kill_name_hint`'s name). A worker that answers by its handle alone still reads alive after its tool died, and the dispatch loop hands it the next test: that test dies against a dead process with a result carrying no `worker_killed` flag. Julia hit this after a kill (D110) and asks its `Popen`; Dymola and OpenModelica hit it with a tool killed while idle, and ask `tool_gone()`. Detach the handle on every path that kills the process, not just on `close()` — a kill empties `self.pids`, and a worker with no PID on record is alive by its handle.

| Method / attribute | Required | Contract |
|---|---|---|
| `__init__(worker_id, config, ...)` | yes | Subclass extends with backend-specific args (e.g. `DymolaConfig`, an interface class). MUST call `super().__init__(worker_id, config)`. |
| `start()` | abstract | Bring the process up, apply startup (library loads, framework settings), hand the process's PID to `self._track_launch(pid)` the moment the process exists — before the first wait on the tool, since a start is given up from another thread and the give-up kills only what is on record (a launch call that returns the PID late gets `launch_grace`, 2 s, to do so) — and name the call in flight in `self._step`. Raise on failure. May block: `start_within` is what bounds it. |
| `startup_timeout` (class attr) | hook | Seconds a start gets when `testing.json` sets no `worker_startup_timeout`. Default `600.0`; no backend sets another. |
| `process_detail()` | hook | What the backend can say about its process when a start or a test did not come back (exit code, step, log tail), read before the kill. Default `""`. |
| `cancel_running(test_key)` | hook | Stop only the running simulation and leave the tool alive: `True` when asked and `run_test` will return on its own within `cancel_grace` (default 10 s). Default `False` — the watchdog kills the worker. |
| `reload(changes)` | hook | Make the worker hold the library as it is on disk — asked before a test the tool's queue submitted against source files newer than the ones this worker read (`changes`: the files that differ, `None` when not known). **Default: restart the worker** (`close()` + `start_within()`), right for any tool; it runs on the dispatch thread like the restart of a dead worker, bounded by the start's bound and uncharged. **An override is a promise to leave the process in place** (Dymola: `openModel` again; OpenModelica: `loadFile` again): it is called on the test's exec thread under its watchdog, so a reload that hangs is the test's timeout and a cancel during it is the test's. It raises `LibraryReloadError(<the tool's message>)` when the library does not load — the test fails with that message, the worker stays up, and the next test asks again, with `changes=None`: after a reload that failed nobody knows which of the changed files the tool took, so the next one must read EVERYTHING (the whole library and each path dependency), and an override keeps no account of its own of a failed reload — and names its step in `self._step` (what the queue shows while it reads). With the default, a restart that fails retires the worker with the start's own reason in the sentence (`Worker dead; restart exhausted — <what start() raised>`), and a Cancel of the test the restart is for gives the start up at its next poll: the test ends CANCELLED and the runner takes a NEW worker object for the slot (`make_worker(worker_id)` again) — a `Worker` whose start was given up is never started a second time, and closes whatever that start still brings up. |
| `is_alive()` | abstract | True iff the worker can accept another test: it holds its handle and its tool still runs — `self._handle is not None and not self.tool_gone()`. The handle alone only for a worker with no process to watch. |
| `run_test(test, test_key, progress=None)` | hook | The blocking single-test execution the watchdog runs on its exec thread. Required unless you override `run_test_with_timeout`. **MUST NOT** raise on test-level failures — return a `TestRunResult(success=False)`. **MAY** raise on worker-level catastrophes (RPC dropped, pipe broken); the template turns that into a `success=False` result and tears the worker down for the dispatch loop to restart. |
| `_detach_interface()` | **required iff the worker holds a handle** | Clear the live-handle attribute and return what it held; runs synchronously inside `close()`. Default `None` = nothing to shut down. |
| `_shutdown_interface(handle, grace)` | required iff `_detach_interface` returns a handle | The graceful shutdown itself (`handle.close()`, `sendExpression("quit()")`, write `{"cmd": "quit"}` + `wait`). Runs on a daemon thread abandoned after `grace`; `close()` raises `TypeError` up front if it is missing, since the daemon thread only logs what it catches. |
| `_evaluate_from_disk(test, test_key, elapsed)` | hook | Rebuild a result from on-disk artifacts after a timeout / worker exception. Default `None` = no disk evidence. |
| `_quiet()` | hook | Context manager wrapped around the watchdog's teardown calls. Default no-op; Dymola's marks the interface it is closing, whose connection errors — the requests still in flight against the killed tool, retried for ~20 s — are then not printed. By interface, not by a window of time: another worker's lost connection still is. |
| `kill_name_hint` (class attr) | hook | Name substring a tracked PID must still match before it is killed — the guard against a recycled PID (`"dymola"`, `"omc"`, `"julia"`). `None` disables it. |
| `timeout_kill_grace` (class attr) | hook | Graceful-shutdown budget on the *timeout* path. Default `1.0`; Dymola uses `5.0` so it can flush its open `dslog.txt`. |
| `exec_thread_prefix` (class attr) | hook | Names the exec thread `<prefix>-exec-<worker_id>`. |
| `run_test_with_timeout(...)` | template | Implemented by the base. Override wholesale only when the execution model doesn't fit "blocking call on a thread" — Julia polls a response queue against a deadline. An override still owes the shared semantics: `before` (the reload of a worker that reads in place) called first through `self._run_before(before, test, test_key)`, whose result — when not `None` — is the test's; no raise on test-level failure, `worker_killed=True` on a timeout kill, `self._kill_tracked_pids()` for the kill, and a `cancelled=True` result when `self._cancel` is set. |
| `start_within(timeout=None)` / `request_cancel(test_key)` | template | Implemented by the base. Do not override. |
| `close(grace=5.0)` | template | Implemented by the base and idempotent. Do not override. |
| `export_fmu(test, output_dir)` | iff `FMU_EXPORT` | Default raises `NotImplementedError`. |

**`PersistentBackendBase`** (subclass of `Backend`) provides the full `run_tests` template:

```
setup_before_workers → _prepare_run (progress + keys + manifest)
    → make_worker × N → start in parallel, each start bounded
    → filter live workers (if zero: fail every test with the reasons, raise)
    → dispatch with restart → close all workers → finalize progress
```

Each dispatch thread drives one `WorkerRunner` — restart a dead worker within its budget, run the test under the watchdog, keep the restart accounting. The simulation pool's threads drive the same object, which is what makes a test fail, retry and restart identically in `dstf run` and in the tool's queue.

You fill in:

| Hook | Default | Override when |
|---|---|---|
| `worker_cls` (class attr) | `None` | Always — set to your `Worker` subclass. |
| `backend_label` (class attr) | `""` | Always — used in headers and thread names ("via persistent **<label>** workers"). |
| `make_worker(worker_id)` | `worker_cls(worker_id, self.config)` | Your worker constructor needs extra args beyond `(worker_id, config)`. Read state stashed by `setup_before_workers`. |
| `setup_before_workers()` | no-op | Backend has runtime patches that must apply before any worker spawns (Dymola: log filter + parallel-startup lock). |
| `preflight(config)` (classmethod) | no-op | Backend has an external dependency beside its executable (a Python module, a license file). Raise `RuntimeError` with an install hint when missing — the CLI surfaces it verbatim. The executable itself is `locate`'s to find and `require_found`'s to insist on. |
| `starting_workers_message(n)` | `"Starting N <label> worker(s)..."` | You want a backend-specific notice (Julia: warmup-time warning). |
| `max_restarts_per_worker` (class attr) | `3` | Restart budget needs tuning. |
| `persistent_backend_cls()` (classmethod) | `cls` | Don't override — the default returns self so the CLI doesn't recurse. |

Plus on the **batch** backend class (the one decorated with `@register("YourBackend")`):

| Hook | Default | Override when |
|---|---|---|
| `persistent_backend_cls()` (classmethod) | `None` (batch-only) | Backend ships a persistent variant. Lazy-import + return its class so the optional dep isn't pulled in during a plain batch run. |

**Inheritance order matters.** Persistent backends inherit from both `PersistentBackendBase` *and* the batch backend. The MRO must put `PersistentBackendBase` first so its `run_tests` template wins over the batch backend's per-test override:

```python
class PersistentDymolaBackend(PersistentBackendBase, DymolaBackend):
    worker_cls = DymolaWorker
    backend_label = "Dymola"
    ...
```

Worked example: `backends/openmodelica/persistent_backend.py` has the most compact of the three persistent backends' class bodies — read it as the canonical reference.

### The simulation pool

`backends/pool.py` — `SimulationPool` is `run_tests`' dispatch kept open, for a caller that queues tests while others run (the authoring tool): `pipeline.open_pool(config, *, backend, store, on_start=None, on_end=None, workers=None)` builds one over the run pipeline's per-item path.

| Call | Does |
|---|---|
| `submit(test) -> QueueItem` | Appends. `Refused` when the test is already waiting or running, or the pool is closed or stopped. |
| `update(item, test) -> bool` | Hands a waiting item the test as it stands now, keeping its place — an item runs with the last test it was given before it started; `False` once it has started. |
| `remove(item) -> bool` | Takes a waiting item out (`REMOVED`); `False` once it has started. |
| `cancel(item) -> bool` | Stops a running item (it ends `CANCELLED`); `False` when it is not running; `Refused`, with a sentence, on a backend without `Capability.CANCEL`. |
| `workers` (get / set) | How many run at once; `Config.parallel` unless given. Raised: more start as items need them. Lowered: the surplus leave as each finishes its item. |
| `waiting`, `running`, `stopped`, `can_cancel`, `path` | The queue in order, the items running, the message the pool stopped with, the capability, the per-item path. |
| `close()` | Waiting items `REMOVED`, running ones stopped (`CANCELLED`), every worker closed, every thread joined. The owner must call it. |

First in, first out. No worker exists before the first `submit`; then as many as there are items, up to `workers`. An item (`QueueItem`) carries `state`, `test_key`, `worker_id`, `run_result`, `result`, `message`, wall-clock stamps and `stopping` — a stop was asked of it and it has not ended, for as long as the backend takes:

| `ItemState` | Meaning |
|---|---|
| `WAITING` / `RUNNING` | In the queue / on a worker. |
| `FINISHED` | The simulation ran; `result` is the result read back. |
| `FAILED` | It failed or timed out (`result` carries the failure), or no worker could start; `message` is the backend's. |
| `CANCELLED` | Stopped while running. Recorded as `last_run_status: "cancelled"` — never "failed" — and read back as no result. |
| `REMOVED` | Taken out while waiting; nothing ran, nothing was recorded. |

`on_start(item)` and `on_end(item)` are called once each, on the POOL thread that ran the item, outside the pool's lock, start before end; an item that never started gets `on_end` only when the pool itself failed it. A callback may call anything but `close()`.

When a worker cannot start, or cannot be restarted, it leaves; when none is left, the waiting items fail with the backend's own message (`No OpenModelica worker could start — worker 0: omc exited (code -9) during loadModel(Modelica); omc log: ...`) and the pool stops taking items.

**How one item runs is the backend's** — one seam, `Backend.item_runner(worker_id) -> ItemRunner`, not a name check:

- a persistent backend returns a `WorkerRunner` over one `Worker` (the default of `PersistentBackendBase`; nothing to override);
- any other backend gets the base `ItemRunner`: nothing to start, and the item goes through `run_single_test` on the pool's thread. Its cancel is `Backend.cancel_test(test_key)`, which the JSON-driver backends implement by killing the test's subprocess (`run_single_test` then returns `cancelled=True`); `PythonBackend` declares `CANCEL`, `FmpyBackend` simulates in-process and does not.

A backend that only runs whole batches (the `.mos` `DymolaBackend`) is refused when the pool is built — hand it the persistent variant.

**What happens around the run on disk** is `pipeline.ItemRun`, `run_pipeline` cut at one test: `begin(test)` keys the test and stamps it pending (`Backend.key_tests`) and wipes that test's stale state only; `end(test, test_key, run_result)` records the verdict and reads the one result back (`Backend.result_of_run`). `status.json` and the dashboard are a list run's snapshot and are not written per item; `pool.path.write_status(items)` writes them for the items given, when asked.

### Selecting a backend

`backends.select_backend(config, *, persistent: bool, to_run: bool = False)` is
the choice itself, and it lives in `backends/` so callers that are not a CLI can
reach it. `to_run=True` — the backend is wanted to simulate with, not only to
read results through — refuses a backend that was looked for and not found
(`Backend.require_found`: `Dymola not found: executable not found — …`) before
anything of a run starts, on either run mode; it is the one such check, and a
backend writes none of its own. It returns
the persistent variant when the registered class declares `PERSISTENT_WORKERS`
and that variant's `preflight` passes, and the batch class otherwise — a
`persistent=True` against a batch-only backend is a no-op, not an error, so one
CLI works across a mixed-backend config. (A `--batch` WRITTEN for such a
backend is the CLI's to refuse: `dstf run --batch` on FMPy or Python is a
`Config error` before this function is asked.)

When the backend cannot be built here it raises `BackendUnavailableError`
(a `RuntimeError` subclass): an unregistered backend name, a backend wanted
for a run and not found, a constructor whose tool is not installed, or a
persistent probe that failed — the last carrying
both fixes, the install hint and `--batch`. "Cannot be built here" is a
`RuntimeError` by contract, the one `Backend.preflight` names; every backend
whose optional dependency may be missing raises that, FMPy's `fmpy` import
included, and `select_backend` translates it. The translation is deliberately
narrow — it wraps exactly `get_backend_class(config)` and exactly the
`cls(config)` call — so a genuine bug inside a constructor still reaches you as its own
traceback.

`cli.run_cmds._get_backend` is the CLI's wrapper over it and nothing more: it
prints the message and exits 1, where a caller with a UI puts the same message
on the action that asked for the run (the tool's Queue button carries it). `Session.open` builds through
`select_backend(config, persistent=False)` — the batch class, since the
Session's backend reads results rather than running them — and turns
`BackendUnavailableError` into `ConfigError`, so `dstf author` on a machine
that cannot build the backend prints one `Config error: ...` line and exits 2.

### The run as a library

`pipeline.run_pipeline(config, tests, *, backend, store, merge=False,
candidates=None) -> RunOutcome` is the run itself, without a CLI around it:
the ref-ID mapping, the stale-state wipe for the tests in scope, the live
dashboard's first paint, `backend.run_tests`, the cross-backend chains, the
`merge` scope widening and the result read. It prints the backend's narration unless
`backend.quiet` is set (below); it never exits the process, never opens a
browser and never prompts; a backend that cannot start raises, and the caller
decides whether that is an exit code or a red panel. It does not score and
writes no `verdicts.json`: `cmd_run` and `cmd_compare` score.
`cmd_run` is its CLI wrapper — selection, the outcome's notices printed,
`--accept` / `-i` / the report. The authoring tool runs through the simulation
pool instead (`open_pool`, below): the same per-test path, one test at a time.

`RunOutcome` carries `manifests`, `scope_tests`, `notices` (the cross-backend
chain's lines, for the caller to show however it shows things) and `results`,
which is keyed by exactly the models in `scope_tests` — wider than what just
ran when `merge` is set, never wider than the scope, whatever else the work
dir's accumulated manifest holds from earlier runs.

The run's real product is on disk, not in the return: `batch_manifest.json`,
`status.json`, `dashboard.html`, `reference_manifest.json` and the per-test sim
dirs. The authoring Session reads a finished run back through
`backend.read_last_results`, so a caller that drops the returned outcome has
lost nothing the next read cannot recover.

Nothing in `backends/` narrates a run to the console unless it is asked to:
`Backend.quiet` (instance attribute, default `False`) suppresses the run header,
the progress bar, the summary, the read phase and the worker-lifecycle lines.
The CLI leaves it false; the authoring tool sets it, because its backend's
console belongs to nobody. **A new run-narration `print` in a backend is gated
on it** — inside one of the `base.py` narration helpers, which return when
`quiet` is set, or under an `if not self.quiet:` of its own. Logging is not
narration and is not gated.

### A comparison mode's hooks for the authoring tool

The fields of `MetricDescriptor` (and of a mode's config dataclass) that
the authoring tool reads, beyond what scoring needs. The numbering is the
one the metric contract had; items 1-6 are on the API page.

7. `edit_handle` (default `None`) is the inverse of the metric's
   `kind="handle"` geometry: what the authoring tool calls when a user drags
   one of its handles on the plot (`comparison/handles.py`). It takes a
   `HandleEdit` — `leaf` (the raw leaf as `metrics` holds it; never mutate
   it), `local` (the handle's id without its leaf path, e.g.
   `"tube/upper/3"`), `x` and `y` (the dragged position in data coordinates,
   `None` for an axis that did not move), `reference` (the primary
   baseline's `(time, values)` at full resolution, unwindowed, or `None`),
   `target` (the number a tolerance side is measured from where the leaf's
   own geometry holds it — a `scalar` leaf's baseline scalar, read off its
   `scalar/reference` line — else `None`) and `span` (the ACTUAL series'
   first and last time as the scorer received it, sliced to the leaf's
   window, or `None`: what bounds a time that is scored against the run)
   and `visible` (what the x and the y axis the position was read on SHOWED,
   as two spans — the page's, at the press or the click; `None` for an axis
   the caller cannot say) —
   and returns
   the new raw leaf, or raises `HandleEditError` naming the handle and the
   reason for a drag the leaf cannot take — with the reason alone as its
   `reason` (`handles._refuse` builds both), which is what the tool shows in
   the open criterion's editor. The tool stages what comes back as
   ONE `replace` of the whole leaf, so an editor rewrites the leaf, never a
   field path, and a leaf equal to the input stages nothing. The builtin
   editors round every float they write to 12 significant digits
   (`handles._written`), so drag arithmetic never lands float noise in the
   author's spec file — after they SNAP it (D149): a value the pointer
   placed goes through `comparison.snap.snapped(value, span, fraction=…)`,
   the one snap rule — `10 ** round(log10(span / 1000))` is the step, a
   coordinate or a side in the axis' units is rounded to a multiple of it,
   a relative side (`fraction=True`) to 3 significant digits, and with no
   span nothing is snapped. Snap first, then clamp and refuse, so what is
   judged is what would be written; an editor of your own should do the
   same with `edit.visible`. The same editor answers three callers with one
   payload: a release, the PREVIEW of a handle still held, and a click that
   sets an armed field — so it must be pure. Leave it `None`
   and a drag of the metric's handle markers and lines is refused ("has no
   on-plot handles"); the builtin `nrmse` does. Only a `Marker` or an
   `HLine` of `kind="handle"`, and a `Marker` or a `VLine` of `kind="grip"`
   (a tolerance's side, whose geometry is described below), reaches the
   editor: nothing else on the plot — a window, a
   box, a result line — takes the pointer in the page, and a drag of one
   that arrives all the same stages nothing. A grip moves on ONE axis: its
   editor gets the released position on that axis and the grip's own
   resolved position on the other, never something to write there. The
   builtin editors are the worked examples: `tube`, `points` and
   `dominant-frequency` move a list entry, `range` and `scalar` a bound
   (`scalar/min|max`), and all but `range` a tolerance's side — a `scalar`'s
   by its lines `scalar/upper|lower`, `kind="handle"`, the others by their
   grips; `event-timing` has only those. `handles._set_side` is the one rule
   for writing a dragged side: a number stays a number, a pair changes that
   side alone, a side dragged across its target is refused.
8. `seed` (default `None`) gives a new leaf its first values, from the data
   it will score: the authoring tool calls it when a criterion of this
   metric is added and when a criterion's metric is switched to it
   (`comparison/seeds.py`). It takes a `SeedData` — `ref` and `act`, each a
   `(time, values)` pair or `None`, already cut to the criterion's window
   when it has one (`SeedData.series()` is the reference, else the actual,
   whichever first has a finite value) — and returns the params to add to
   `{"metric", "variable"}`, or `{}` when there is nothing to seed from or
   nothing worth seeding: `dominant-frequency` leaves a peak in the lowest
   three FFT bins unseeded, a trend's leakage rather than a frequency. It
   is pure, like `compare`. Leave it `None` for a metric valid bare: the
   builtins `nrmse`, `points` and `event-timing` do. The builtin seeds are
   the worked examples: `range` brackets the data, `tube` a band around the
   reference, `scalar` the final value and `dominant-frequency` the
   strongest peak. A bound or a width is rounded AWAY from the data at the
   leading digit of the margin it leaves, so the spec file reads short; a
   seeded leaf passes on the data it came from — a seed that reads the
   actual, as `scalar` does when the reference has no finite sample in the
   window, does not score against the baseline.
9. A config field holding a list of row dicts declares `columns` in its
   metadata — `(key, type)` pairs, one per row key, where `type` is
   `"float"` (a key the metric indexes: required, a blank cell is never staged),
   `"float?"` (read with `.get`: optional, a blank cell omits the key),
   `"sides"` / `"sides?"` (a tolerance — a number or a `{"lower", "upper"}`
   pair — required or optional likewise, drawn as two fields and a link
   toggle) or a tuple of choices whose first is the metric's default. A
   `sides` entry may carry a third element, what its two sides are called,
   lower first: `("time_tolerance", "sides?", ("before", "after"))`; without
   one they read `−` / `+`. Read an entry by index, never by unpacking two.
   The authoring tool's
   Edit on plot tab draws a table from it, with add and remove rows. The table
   holds a blank required cell locally — marked on the cell, nothing submitted
   until it is filled — and the capability's `act` still refuses one when called
   directly. `columns`
   is read from `dataclasses.fields`, not through `derive_schema`, so it
   never reaches `spec schema`. A list field without `columns` gets no
   table; Edit criteria shows every list field read-only either way.
10. `drawn_by_default` (default `True`) says whether the authoring tool's
    Test page draws a closed criterion of this metric on its plot before the
    swatch on its row is touched. Set it `False` for a metric whose drawing
    crosses the whole plot — the builtins `range` and `scalar` draw bounds
    from edge to edge, which would cover every plot they sit on. It is only
    the default: a failing criterion draws whatever it says, the open one
    always draws, and the swatch overrides it either way. The report does
    not read it.
11. A tolerance is a number or a lower/upper pair (D147), and travels as
    `comparison.sides.Sides(lower, upper)`. `build` is
    `Callable[[dict, Sides], ComparisonMode]`: its second argument is the
    leaf's resolved tolerance as a `Sides`, a number in the spec being both
    sides — a metric with one ceiling refuses `not tolerance.equal` with a
    `ValueError` (the leaf's config error) and reads `tolerance.upper`, as
    `nrmse` does. Read any other tolerance field of your own through
    `parse_sides(value, "<field name>")`, the one reader: its `ValueError`
    names the field and is that leaf's config error when raised from `build`
    or the mode's `__init__`, on both scoring paths; raised later, from
    `compare`, it fails that leaf or variable all the same. Type the config
    field `Sides` and
    `derive_schema` classifies it `"sides"`: the form draws two fields and
    a link toggle (`FieldSpec.side_labels` names them, from the field's
    `side_labels` metadata, `−` / `+` by default), and `spec schema`
    writes `oneOf` a number or an object with exactly `lower` and `upper`.
    The meaning is the metric's to keep: with `d = actual − target`, pass
    iff `−lower ≤ d ≤ upper`. `VariableComparison.tolerance_used` is a
    `Sides`: the constructor takes a number too, but a metric that ASSIGNS
    it assigns a `Sides`. Where it leaves Python as data, write
    `as_written(sides)` (the number when the sides are equal, else the
    pair); in a sentence, `sides_text(sides)`.
12. Geometry of `kind="grip"` is the resolved position of one SIDE of a
    tolerance: a `Marker` on a value's or a time's edge, a `VLine` on the
    edge of a window that spans its panel. Its local id is
    `<family>/<edge>/<key>`, the edge one of `upper` / `lower` (a value: it
    moves on y), `before` / `after` or `early` / `late` (a time: on x; a
    `VLine` always moves on x) — `points/upper/3`, `events/late/0`,
    `tube/lower/const` — and its group is the id without the edge
    (`points/3`), which is also how the handle it stands around reads
    (`points/target/3`). A grip is never a trace and never drawn in a
    figure that is not editable, so emitting one changes no report page; in
    the tool it is an arrow just outside its edge, drawn for the open
    criterion only and shown while the pointer is near its group. A side
    that should be drawn on the report too is a `kind="handle"` line
    instead, as the scalar's `scalar/upper` and `scalar/lower` are.
13. `place_row` (default `None`) is what a Ctrl+click on the plot adds to
    the metric's table (D149): it takes a `handles.RowPlace` — `leaf`,
    `blanks` (per list field, the row the editor's Add row would append:
    every field the click does not set), `panel` (`"time"` /
    `"spectrum"`), `x` and `y` in that panel's axes, `reference` and
    `actual` (`(time, values)`, unwindowed), `geometry` (what the plot
    draws for the leaf: where the builtins find the run's events and its
    spectrum), `visible` and `span` as `HandleEdit` has them — and returns
    the leaf with one more row, SETTLED ON THE DATA rather than on the
    pixel, or raises `HandleEditError` where the click names no place for
    one. Return the leaf unchanged to add nothing. `row_noun(leaf)` says
    what such a row is called ("a point") for the editor's hint, or `None`
    where a click adds nothing to that leaf (the builtin tube of constant
    width). `handles.handle_fields(leaf, local)` is the third inverse: the
    field of the leaf a handle writes on each axis it moves on, as a path
    of keys — how "arm a field, then click the plot" finds a field's
    handle; a handle of your own that it does not know is simply not
    reachable from a ⌖.

### Capability contract (authoring tier)

The authoring tool is a plug-in surface too. A **capability** is one thing an
author can do to a project — edit a criterion, edit the tree, accept results,
queue a selection, drag a tube's control points — and it registers through the
same `dstf.plugins` entry-point group a metric does. A capability decides
whether it is available, builds its own controls, and returns an *outcome*; it
applies nothing itself, so the session stays the single place where a spec is
mutated, a store is written, or a test is queued.

The contract lives in `dstf.authoring.capabilities`: the `Capability` protocol
(`name`, `label`, `placements`, and `available` / `caption` / `panel` / `act`),
the `Placement` enum (`SUITE`, `TEST`, `VARIABLE`, `CRITERION`, `NODE`,
`STATISTIC`, `STATISTIC_CRITERION`, `TREE_CRITERION`, `FAMILY_CRITERION` —
where the capability attaches; a capability names a frozenset of them), and the
five outcomes `act` may return — `Patch` (ops for the Session to stage),
`StoreWrite`, `Enqueue`, `Artifact`, `OpenProject` — or `None`, or a sequence of
them. `Enqueue(models=...)` hands the Session tests for its simulation queue
(D150), in the order to run them; the Session appends them — one already
queued or running is skipped — runs them on its pool, and reads each result
back and rescores that test as it ends. A capability starts no thread and no
run of its own: the `Job` outcome that did (with `JobContext`, `after_reload`
and the Session's lock on a job's models) is gone. `StoreWrite` carries
`models: frozenset[str]`, the models it touches: one the queue holds refuses
it. Every outcome is keyword-only, so a field added later
cannot silently take a positional argument's place. `caption(view)` is the
button's label — object, effect and count, `plural()` for the noun — and
`available` the sentence beside it when it is greyed. The view's `selection`
is the page's that built it (D129): `SessionView(session, selection)`.

**The placements** (D129, D131). Each one says how the shell draws a capability
and what the view it hands over carries; a capability chooses the placements,
never the way it is drawn there — save its optional `emphasis`, which says per
situation of the Test page whether it is the next action there (drawn filled)
or belongs in the status strip's ⋮ menu.

| Placement | Where | How it is drawn | `selection` carries | `panel.figure` |
|---|---|---|---|---|
| `SUITE` | the Suite page's batch bar | a button with the `caption`, panel in a dialog | `tests` (the checked tests, possibly empty) | raises on `on` |
| `TEST` | the Test page's status strip, under its header row | a button with the `caption` (filled, quiet or in the ⋮ as its `emphasis` says), panel in a dialog | `tests` — exactly the one test of the page | raises on `on` |
| `VARIABLE` | a plot card's criteria header, for the plot's first tracked series | inline: the panel alone at the header's right end (Edit tree's `+`), its forms a row under the header — no dialog | `tests` (one), `variable`, and `texts` (the page's scope, so the panel reads the variable as the page does) | that card's plot |
| `CRITERION` | the open criterion's editor, below its row | inline: the panel alone, no button, no heading | `tests` (one), `variable`, `leaf_path` | the plot the row sits under |
| `NODE` | a tree node's row, and a criteria row under a plot | inline: the panel alone, no button, no heading | `tests` (one), `node_path` (a leaf OR a combinator), and `variable` on a criteria row | raises on `on` |
| `STATISTIC` | the end of a Statistics-card row no criterion reads (D143) | inline: the panel alone (Edit tree's `+`), no button, no heading | `tests` (one), `quantity` (the row's quantity path, e.g. `translation.nonlinear_count`) | raises on `on` |
| `STATISTIC_CRITERION` | the editor of a criterion scored through a statistic, under its Statistics row (D143) | inline: the panel alone, no button, no heading — CRITERION's editor with no plot, so a capability that needs one is not offered there | `tests` (one), `leaf_path` | raises on `on` |
| `TREE_CRITERION` | the editor of any criterion opened under its row in the criteria tree (D146) | inline: the panel alone, no button, no heading — CRITERION's editor with no plot, like `STATISTIC_CRITERION`'s | `tests` (one), `leaf_path` | raises on `on` |
| `FAMILY_CRITERION` | the editor of a folded criteria row under a family's plot: one criterion on several elements of an array (D152) | inline: the panel alone, no button, no heading — CRITERION's editor for every member at once, with no plot (nothing of twenty criteria is dragged), so a capability that edits ONE leaf is not offered there | `tests` (one), `leaf_paths` (every member, in tree order), `leaf_path` (the first) and its `variable` | raises on `on` |

An inline panel (`VARIABLE`, `CRITERION`, `NODE`, `STATISTIC`,
`STATISTIC_CRITERION`, `TREE_CRITERION`, `FAMILY_CRITERION`) draws no heading and no button
of its own frame: the row it sits in already names it — the criterion's row above
the editor, the plot's "Criteria" header — and a second name there makes one
editor read as two panels. Its `caption` is never read. Where a
button placement greys its button and puts `available`'s sentence beside it, an
inline placement shows that sentence *in place of the panel*, so the control
keeps its place in the row and says why it cannot be used.

`act` returning `None` means nothing happened: the host applies nothing, rebuilds
no panel and leaves an open dialog open — there is nothing to close over. It is a
real answer, not an oversight (every plot sends one `{"autosize": true}` relayout
when it mounts, and a subscribing panel answers that with `None`), so a capability
that returns `None` on purpose says why in its own panel.

`StoreWrite.run` returns `str | None`: the one line the queue panel shows, saying
what the write did — what it stored and what it refused, with the reason. A
partial write has no other way to report its skips, and raising after some
baselines were already stored would say something false. The Session appends
that line to its notices and emits `notice` with that `message`; returning
`None` says there is nothing to report and appends nothing.

The view carries the panel's other readers: `notices`, the panel's lines
oldest first — what each store write reported, plus the open-time warning where
there was one — and `artifacts`; and the queue as a capability may read it:
`queue_state(model_id)` — `ItemState.WAITING` or `RUNNING` while the queue
holds the test, how its last run there ended until that row is cleared, else
`None` — and `queue_refusal`, why nothing can be queued on this backend. A
queued or running test refuses nothing but a `StoreWrite` on it: `available`
has no lock to answer for. `notices`, `artifacts` and the queue are per
project: opening another one resets them at the commit point, so one
library's panel is never shown
under another's, and an open that fails leaves the live project's as it was.

`backends.progress.read_status(work_dir)` is the one reader of `status.json` —
every reader comes through it, and it never raises, because a UI timer calls
it.

Register with `register_capability(cap)` from a module the `dstf.plugins`
entry-point group loads, exactly as a metric calls `register_metric`; a duplicate
name is an error rather than an import-order race.

A capability receives a `SessionView` — readers only — and applies nothing
itself: the Session is the single place a spec is mutated, a store is written or
a test is queued. The view carries what a panel draws from: `comparisons`,
`figure(model_id, variable)`, `tree_view(model_id)` — the read-only tree the
report page and the shell both show, built by the report's own function — and
`tree(model_id)`, a copy of the raw tree a `Patch` edits (the entry's `metrics`
block, else the tree a rescore would materialize for a test the flat path
scored). `rescore` is the one reader that writes — it stages that
materialization — so `available` must not call it.

**Building a panel.** `Panel` is a container the capability owns entirely: it
builds its controls into the slot its host owns — a dialog's, or an inline
host's column — and may subscribe to the plot it is drawn beside through
`panel.figure.on(kind, handler)`, whose handler receives the primitive's id and
a payload. When a control fires, the capability calls `panel.submit(**inputs)`:
the shell calls `act(view, **inputs)` and hands what comes back to
`Session.apply`. A capability never calls `act` or `apply` itself, and an
exception from either is shown with the panel — above an inline one, in the
dialog of a button placement — with the Session unchanged; the next successful
submit hides it in place, without rebuilding the panel, so the field being typed
in survives. `submit` returns whether it went through: `False` when `act` or the
apply raised. Import the UI framework inside `panel`, not at module level, so the
module stays importable without the `[author]` extra — the built-ins do.

**What a panel shows that the spec does not hold.** A panel is rebuilt by edits
it did not make — a drag on the plot rebuilds the whole criterion editor, another
panel's submit rebuilds this one — and is never told. Built from the view alone,
whatever its controls held beside the spec is gone: a required cell cleared to be
retyped comes back filled. `panel.kept` is a dict of the panel's own that
outlives it: write there as a control becomes such a thing, read it back when the
panel is built. The host hands the same dict to every build of that capability's
panel for the same node of the same test, re-keys it when a tree edit moves the
node, and drops it — rebuilding the panel on an empty one — with the node and
when an edit that is not this tab's lands on the test (a Discard, a Save, another
tab's). Record beside each entry what the spec held under it, and ignore an entry
whose spec value has since moved. The record is the browser tab's, not the
page's: it outlives a switch of test and a visit to the Suite page, and is there
when the criterion is opened again in that tab. Only the criterion editor under
a plot keeps one (Edit on plot's table records its blank and half-typed cells
there, Edit criteria's form a tolerance with one side typed). Every other host hands a fresh dict to every build — a dialog, a row,
and the two other criterion editors too: the one under a row of the criteria
tree and the one under a row of the Statistics card — so a panel placed at
several of these must work with a dict that is empty each time.

**A form row.** `panel.row(title, build)` opens a form below the row the panel
is drawn in — how Edit tree's add, wrap and edit forms open under a tree node's
row or a criteria row (D133), and its add form under a plot's criteria header. `build(close)` draws the form's content; `close`
takes the row away. Call `close` after a `submit` that returned `True`, never
before it: a refused act then leaves the form open with what was typed, and its
error shows under the row. One thing is open at a time on a page, so opening a
row closes any other row and the open criterion's editor, and opening an editor
closes the row. While the row is open the page calls `build` again, into an
emptied row, when an edit staged elsewhere moves the node the panel is for, so a
form never shows a value its node no longer has; build its fields from the view
each time. The row also closes when its host is rebuilt, when its row is hidden
and when its node is gone. In a dialog (a button placement) the form is drawn
in the dialog, below the panel; the criterion editor has no row, and `row`
raises there. `panel.retitle(title)` says the open row's title again, for a
form whose subject follows a choice made in it (the add form's `Add a
criterion for pipe.T[1:20]` follows its Indices); it does nothing while no
row of the panel's is open. `panel.reveal(leaf_path)` asks the page to open that criterion's
editor once the page has drawn it — after an add, the new criterion — on the
plot the panel's row is on when that plot draws the criterion's variable, else
on the first plot that does, and to scroll it just into view. It records the
path only when that node exists, so call it after the `submit` that made it;
where no page reveals criteria (a dialog, the criterion editor) it does nothing.

**A lead place.** `panel.lead(build)` draws `build()` in the node's lead place:
a tree row's, right after its status pill, where the node's kind is read — how
Edit tree draws a combinator's kind as a dropdown there. The place is emptied
before each draw and with the panel's host; while it holds something the row's
text leaves the kind out (`3 criteria`, not `and · 3 criteria`), and a host that
draws nothing there — Edit tree where it can only say why — leaves the row reading its
kind. A row without a lead place (a leaf's, a criteria row's, a plot's header,
a dialog) raises, as `row` does where there is no row.

**Build from the view, every time.** An inline host rebuilds every capability's
panel in its slot whenever what they show can have moved — after a staged edit,
Discard, Save, Reload or open, once the comparisons are rescored — except the
acting capability's own panel while an act from its own controls runs, so a
field being typed in survives; when that act came from the figure hook (a drag,
during which nothing is being typed) the acting panel is rebuilt too. A dialog's
panel is not rebuilt while the dialog is open: a confirm list belongs to the
moment of the click, and an act that applied something closes the dialog. When
the set of capabilities placed there changes, or an `available` answer moves
without the tree changing shape, the host rebuilds
them all. So a panel must read what it shows from the view on each build, never
from state it kept from an earlier one: a panel that captured its leaf when
first built would show — and on its next change re-stage — values another
capability, a Discard or a Reload has since replaced. State a panel does keep
between its own acts (a table's rows while rows are added) is fine, because the
next rebuild reads the Session again. A panel's hook handlers go with it: its
host registers them under that capability's own owner key and clears exactly
those when it rebuilds the panel, when an inline host is emptied, and — for a
button placement — however its dialog closes, so a panel nobody can see never
stages an edit from a drag.

**The figure hook's payloads.** `panel.figure.on(kind, handler)` calls
`handler(primitive_id, payload)` for three kinds:

- `plotly_click` / `plotly_selected` — the id of the first point whose trace
  `meta.kind` is `"handle"`, else of the first point (its `customdata`, then
  `meta.id`, then `curveNumber`), with the event payload. Under the figure's
  x-unified hover a click lists every trace at that x, so a handle is not
  necessarily the first point.
- `plotly_relayout` that moved shapes — once per shape, ascending by index.
  Plotly's payload names the shape only by index; the shell resolves the id
  from the rendered figure's `layout.shapes[N].name` (`""` when it cannot) and
  hands over that shape's keys without their `shapes[N].` prefix, in data
  coordinates: `{"y0": 6.0, "y1": 6.0}` for a line dragged whole, one end's
  keys for a line dragged by an end, `{"xanchor": 4.0, "yanchor": 2.99}` for a
  handle ring or a grip's arrow moved. Both are pixel-sized `path` shapes,
  which move from wherever they are pressed and never resize; read one only
  by `xanchor` / `yanchor`, and a grip only by the anchor on its own axis.
  On the Test page the release passes through the page's `grips.js` first:
  an axis the pointer travelled under 8 px along is sent as `None`
  (unchanged), and a position let go outside the plot area is held to the
  axis range the plot had when it was pressed.
  The shell interprets no positions; the capability does, from the handle's
  primitive type. After the handlers run, the shell re-sends the figure
  whether or not anything was staged, so a drag that changed nothing snaps
  back.
- Any other relayout (a zoom) — once, with id `""` and the raw payload.
- `dstf_move` (D149) — a handle HELD on the plot, at most about 15 times a
  second: once per shape, with the payload its release would carry — so one
  function reads both — plus `drag`, the number of the drag it is of; or
  once with id `""` and `{"end": True}` for a drag that ended with no
  release. A move is a preview: stage nothing from it. The hook numbers
  every move and release (the page's sequence) and DROPS a move numbered at
  or under the last release or end it heard, so a preview that arrives late
  never paints over what the release staged.
- `dstf_click` (D149) — a click on the plot that is no double-click, ends
  no drag, is on no draggable shape and is inside a panel's plot area: id `""`
  and `{"xaxis", "yaxis", "x", "y", "xrange", "yrange", "ctrl", "shift",
  "alt", "meta"}` — the axes of the PANEL under the pointer (`"x"` / `"y"`
  the time plot, `"x2"` / `"y2"` the spectrum), the position in them and
  what they showed. The shell decides nothing about a click; Edit on plot
  takes a Ctrl (or ⌘) click as "add a row".
- A release's and a move's per-shape payload also carries `visible`,
  `{"x": span, "y": span}` — what that shape's axes showed at the press
  (`None` for an axis it is not on in data) — and `seq`, its number.

**A click that sets a field, and a preview (D149).** Four `Panel` members
are the editor's side of the plot; each is a no-op where the panel is beside
no plot (a dialog, a tree row, a Statistics row):

- `panel.place_button(pointer, label)` draws a ⌖ into the current slot — a
  number field's `append` slot is the place (`with field.add_slot("append")`)
  — that ARMS what you call `pointer`; `label` is what the hint line and
  the line under the plot say is being set. Draw one only where a click
  can set the thing in the state it is in: the builtins ask
  `edit_on_plot.placeable(view, model_id=…, variable=…, path=…)`, the rule
  `placed` acts by, and name a criterion's field by its keys into the leaf
  (`("tolerance", "upper")`, as `handles.handle_fields` names one) and a
  table's row by its table and index (`("points", 3)`).
  `render_form(..., place=fn)` offers `fn(pointer, label, field)` every
  number field and tolerance side. `panel.can_place` says whether the
  panel is beside a plot at all.
- `panel.on_place(handler)` — `handler(pointer, click)` is called for a
  PLAIN click on the plot while something THIS panel drew a ⌖ for is
  armed; `click` is the `dstf_click` payload with `label`, the armed ⌖'s.
  Return `submit`'s answer: `True` disarms, `False` (refused) leaves it
  armed and the refusal is said under the plot. `edit_on_plot.placed(...)`
  turns a pointer and a click into the new leaf, and refuses a click on
  the panel the thing does not belong to.
- `panel.disarm()` — for a panel to call when what a ⌖ named moved under
  it with no edit saying so (a table's row added or removed while its
  list is held back, its fields swapped for another mode's). Every edit
  of the test disarms by itself.
- `panel.preview(leaf, revision)` hands every panel of the editor the leaf
  a held handle WOULD write (`None`: over); `panel.on_preview(handler)`
  receives it. Show it with `TypedNumber.preview(value, revision)` /
  `SidesControl.preview` / `Form.preview`, which change what a field SHOWS
  and never its value — nothing reaches `on_change`, nothing is staged —
  and which leave a field typed in since that `revision` (the drag's
  number) alone. A preview is a METHOD CALL on the field's component, not
  a prop: in NiceGUI 3.16 a prop write resends the element and the browser
  renders the page's whole tree again (50–77 ms with a 30-row table), so
  anything of your own that follows a held handle should do the same.

In the tool's figure each `kind="handle"` Marker of a leaf named in
`rings_for` gets a ring shape named with its id, and each `kind="grip"`
primitive of such a leaf an arrow shape named with its id — on the Test page
that is the open criterion alone (D131); lines, boxes and windows keep their
own names. plotly.js makes every shape of an editable figure draggable and has
no working per-shape switch, so the figure says which of its shapes take the
pointer, in `layout.meta.dstf` (`figures/plotly.py::POINTER`):

- `grips` — `{shape name: {"group", "axis", "label"}}` per grip arrow: the
  group it shows with, the axis it moves on (`"x"` / `"y"`), its tooltip;
- `targets` — `{ring name: group}` for each ring a group's grips stand
  around;
- `draggable` — the list of shape names an editor moves: the ringed leaves'
  rings, grips and `kind="handle"` lines.

The Test page's `grips.js` makes every shape the map does not name draggable
inert — and a line it does name, while that line is off the plot — and shows
a group's arrows only near it; a figure with no map is left as Plotly draws
it. The map rides in the figure so that it is always the map of the figure
the browser holds. The
handler semantics are the hook's own, not the DOM's: each shape is dispatched
from a snapshot of the handlers taken when its dispatch starts; a handler
registered since is not called for it, one removed since (its panel rebuilt) is
skipped, checked by identity; a handler registered twice is called twice; a
handler that raises ends the relayout — later handlers and shapes are not
dispatched — and the snap-back still runs. A panel rebuilt by an act its own
drag triggered therefore registers its new handlers immediately and is not
called again for that event.

**Forms.** A panel over a typed config need not hand-build its controls.
`derive_schema(config_cls, mode=...)` (`comparison/mode_schema.py`) describes
the dataclass, and `render_form(schema, values, on_change)`
(`authoring/forms.py`) turns each field into one NiceGUI control, calling
`on_change` with the full values dict after every edit. A dataclass default is
shown as the control's placeholder and never written; `ui_min` / `ui_max`
validate rather than clamp; `passthrough` fields are shown read-only; a
`sides` field is `forms.sides_control` — two number fields and a link toggle
whose state is the shape of the field's value: a number while linked, a
`{"lower", "upper"}` pair while not; a pair with one side typed changes
nothing until the other is. The same
`derive_schema` feeds `dstf spec schema`'s JSON-Schema emitter.

The six shipped capabilities live in `authoring/builtin/` and register through
this same call — Edit criteria, Edit tree and Edit family (the folded row's
Indices, at `FAMILY_CRITERION`) return a `Patch`, Accept a
`StoreWrite` over the shared accept function, Queue (`EnqueueTests`, named for
what it does: "queue" is the shell's word for the queue itself, and the shell
names no capability) an `Enqueue`, and Edit on
plot a `Patch` from a metric's `edit_handle`, its `place_row` or a table's
rows — the one builtin client of the figure hook's events (Edit criteria
arms its own fields and shows previews through the `Panel` alone).

§4.6 and §4.7 of [the two-tier architecture
design](superpowers/specs/2026-09-08-two-tier-architecture-design.md) have the
full specification; `docs/guides/criteria.md` shows the tool they build.

---

### Cross-cutting guarantees

Every plug-in, regardless of layer, must honor:

1. **Determinism** — same inputs produce the same outputs. No wall-clock, no PID, no randomness without an explicit seed.
2. **JSON-serializable configuration** — all plug-in config must round-trip through `test_spec.json` and the reference JSON.
3. **Clear error messages on misuse** — "Backend `Foo` does not produce `Events`; metric `event-timing` requires it" is a framework-level check, not a runtime crash.
4. **No framework-core edits** — if adding a plug-in requires editing the `cli/` package, `config.py`, or anything outside its own package and the registry it registers with (`backends/`, `comparison/registry.py`, `discovery/recognizer.py`, `authoring/capabilities.py`), the abstraction is wrong. File an issue before working around it.
5. **Test fixture included** — every plug-in ships with at least one test against a known-good input.

---

### Where to put new code

```
src/dstf/
  backends/
    <name>/           # all tool-specific code for backend <name>
  comparison/
    modes.py          # the built-in metrics' modes and typed configs
    builtins.py       # their MetricDescriptors, registered at import
    metric_tree.py    # the built-in combinators
  discovery/
    <name>.py         # one file per strategy
  authoring/builtin/
    <name>.py         # one file per shipped capability

examples/
  modelica/
    ModelicaTestingLib/    # Modelica demo library (also used as pytest fixture)
  fmu/
    reference-fmus-binaries/   # FMI org's Reference-FMUs (fetched by scripts/fetch_reference_fmus.py)
  julia/
    JuliaMtkTestingLib/    # ModelingToolkit demo library
  python/
    PythonTestingLib/      # Python demo library (scipy ODEs, a CSV data loader)
```

Each directory under `examples/` demonstrates one Source type end-to-end — a working reference a user can copy. Pytest fixtures (captured artefacts for unit-testing internals like the MAT reader) remain under `tests/fixtures/` and are separate from `examples/`.

Third-party plug-ins live in separate packages and register via the `dstf.plugins` entry-point group (`docs/guides/plugin.md`). The same layout applies there.
