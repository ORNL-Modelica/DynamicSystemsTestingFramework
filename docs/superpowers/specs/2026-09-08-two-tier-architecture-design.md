# Two-tier architecture — Design

**Status**: Design — revised after fresh-context review; pending owner approval
**Date**: 2026-09-08
**Supersedes**: the "Workflow boundary (hard)" of D66 (reporter as authoring
IDE; no local server). D66's per-leaf modularity and auto-derived controls
survive; its browser-side live preview and its parity floor do not.
**Depends on**: the tube union-grid fix (503cca6), which made the browser's
tube verdict diverge from the CLI's and so made this split urgent.
**Adjacent**: docs/ideas.md entries "Two-tier split", "The authoring tier is a
front end, not a criteria editor", "Where does DSTF end and the next tool
begin?", "Report → CLI action menu", #29 (optional server), #42 (quick-save).

---

## 1. Summary

DSTF's HTML report is today both the viewing surface and the authoring
surface for acceptance criteria. The authoring half is written in JavaScript
and re-implements the Python scorers, geometry and event detection so the
browser can give live feedback. That duplication is guarded by a parity suite
and has still drifted: the CLI now scores tubes on the union grid, the browser
on the reference grid.

This design splits the reporter into two tiers along one line: **mutation**.

- A **reporting tier** that mutates nothing. Static HTML, served from
  `file://`, drawing figures Python already resolved. Viewing interactivity
  (zoom, filter, toggles, copy-a-command) stays.
- An **authoring tier**: a local Python server with a browser front end,
  wired directly into the framework. Everything that edits a spec, writes a
  baseline or launches work lives here, behind typed outcomes the Session
  applies.

Both tiers consume **one figure layer**: each comparison mode returns the
geometry it scored against as data, and one Python module turns that data
into a Plotly figure dict. No scoring, interpolation or event detection
survives in the browser. `tests/test_scorer_parity.py` is deleted, not
maintained.

The authoring tier is designed as a **front end, not a criteria editor**: a
fixed shell renders registered *capabilities*, and a capability that does not
exist yet attaches through the same registry without restructuring. This is a
requirement on the design (§4.6, §7), not an aspiration.

## 2. Motivation

Measured on the tree at a91275f:

- The JavaScript and HTML under `src/dstf/reporting/templates/`, vendor
  excluded, is about 6,900 lines. `scorers.js` (1,225), `editors.js` (2,420)
  and `tree-ui.js` (1,441) exist to author; `plots.js` (227), `dashboard.js`
  (581), `dashboard.html` (290), `interactive.html` (443) and about half of
  `export.js` (276) report. Roughly three quarters of the lines serve
  authoring.
- Three JavaScript registries are keyed on the same mode string
  (`MODE_SCORERS`, `MODE_PLOT_CONTRIBUTIONS`, `MODE_PLOT_EDITORS`). The first
  two duplicate Python wholesale; the third is drag plumbing whose tube
  resolver (`editors.js` `resolveAllBoundsOnGrid`) mirrors
  `algorithms._tube_bounds`. Tube bounds therefore exist once in Python and
  three times in JS. Points resolution, FFT and peak finding, event detection
  and windowed NRMSE each exist twice.
- Python ships raw `act` / `ref` arrays, plus the dominant-frequency spectrum
  and peaks, which the browser uses only as a fallback below four samples.
  Every bound, box and marker the page draws is recomputed client-side;
  Python computes the same values during scoring and discards them.
- Adding a mode touches the three JS registries plus three mode branches in
  Python: `_extract_mode_values` and `_leaf_score_display` in
  `context_builder.py`, the spectrum gate at `context_builder.py:418`, and
  the decimation gate at `report_suite.py:335`. `scalar` is in the Python
  chains but in none of the JS registries, so it has no drawing.
- The browser scores tubes on `ref_time`; the CLI scores on the union grid.
  The page can show a passing tube the CLI failed.
- The record's earlier framing of "three renderers" (matplotlib in the review
  loop and the report writer, Plotly in the templates) was stale: no
  `import matplotlib` exists under `src/`. There is one renderer and the
  duplication is Python-versus-JavaScript. `matplotlib` is a dead dependency.

The owner's rulings that shape the design: all authoring moves out, and
minimal function is argued *back* to the report later, never the reverse;
the line is mutation, not interactivity, so filtering, zoom, sweeping a
selection into a rerun command and copying values off a plot are viewing and
stay; the authoring tier must accommodate capabilities it was not built
around; `tube_points` stays readable, never writable.

## 3. Scope

### In scope

- Geometry primitives returned by every comparison mode; a summarize hook on
  the metric descriptor replacing the mode branches.
- A figure layer producing Plotly figure dicts, consumed by both tiers.
- The reporter reduced to viewing; the authoring JavaScript, embedded forms,
  patch download, CLI-authoritative badge and parity suite deleted.
- The authoring package: Session, outcomes, capability protocol and registry,
  the NiceGUI shell, and four v1 capabilities: edit criteria, edit tree,
  accept, rerun.
- `dstf author` CLI command.
- Four seams the authoring tier needs that do not exist today, each its own
  commit: spec parsing from an in-memory document (§4.5); a multi-model patch
  envelope (§4.7); the accept guard shared between CLI and Session (§4.7); a
  run callable that neither exits, prints, nor opens a browser (§4.7).
- On-plot editing (tube control points, range handles, point and peak
  markers) as a capability attached after v1, exercising the figure event
  hook the shell exposes from v1.

### Out of scope, and must not be precluded

- Report review inside the tool. v1 links to the dashboard; a second
  rendering of the report is not built.
- A recent-projects / known-libraries notion, and a new-library wizard. The
  Session's project is nullable, the shell has a project-less state, and the
  `OpenProject` and `Artifact` outcomes exist from v1 so both can attach.
- Uncertainty quantification and test sweeps. See §9: the design is expected
  to *inform* where DSTF ends, not to decide it.
- A JSON API over the Session. It is itself a capability and attaches when
  something outside the tool needs it.
- Cancelling a running job. No cancel path exists in the backends today; it
  is named backend work (§9), not promised by the tool.

### Out of scope, full stop

- Any back-compatibility path for the deleted browser editors. Patches
  exported by the old page still apply through `spec-update`, because the
  per-model envelope stays readable (§4.7).
- Windows-specific work. Nothing here touches simulation.

## 4. Design

### 4.1 The line

A surface either mutates or it does not.

| Operation | Tier |
|---|---|
| zoom, hover, legend and overlay toggles, error overlays, values off a plot | reporting |
| filter tests, sweep a selection, copy a CLI command | reporting |
| read the metric tree with verdicts and score text | reporting |
| change a tolerance, a mode, a window, an `against` | authoring |
| add / remove / wrap leaves, change a combinator | authoring |
| accept results as baselines | authoring |
| run or rerun tests | authoring |

The CLI remains the execution surface and keeps its own mutations (`--accept`,
`spec-update`, `run -i`). The terminal review loop is not a report.

### 4.2 Geometry

**Geometry is a result.** `VariableComparison` gains

```python
geometry: tuple[Primitive, ...] = ()
```

and each mode's `compare` returns the primitives it scored against, from the
arrays it already has. Primitives live in `src/dstf/comparison/geometry.py`,
next to the modes that emit them, and are frozen dataclasses with a fixed
vocabulary. Every primitive carries `id`, `kind` (a free label the builder
may style on: `"result"`, `"handle"`, `"ref"`, `"act"`, `"peak"`, ...) and
`panel: "time" | "spectrum"` (default `"time"`).

| Primitive | Fields beyond id / kind / panel | Emitted by |
|---|---|---|
| `Band` | `time, lower, upper` | tube (on the union grid it scored) |
| `Box` | `t0, t1, y0, y1, passed` — `y0`/`y1` `None` means full panel height | points (checkpoint box), event-timing (band around each reference event), dominant-frequency (acceptance band per declared peak) |
| `Marker` | `x, y, passed` | points (targets), dominant-frequency (declared, detected and matched peaks), and every mode's **handles** (below) |
| `HLine` | `value, passed` | range (min / max), scalar (bounds, or the baseline value) |
| `VLine` | `time` | event-timing (`ref` / `act` events) |
| `Series` | `x, y` | dominant-frequency (reference and actual spectra); the view's error series |
| `Window` | `start, end` — either may be `None` | the evaluator, not a mode (the window lives on the leaf) |

`nrmse` emits nothing of its own. A new primitive kind is an explicit
addition to this file plus one trace builder in §4.3; the vocabulary is
deliberately small so that adding to it is cheaper than smuggling shapes into
`Series`.

**Handles are geometry too.** The resolved positions of a mode's authored
parameters — tube control points (upper and lower separately), range bounds,
declared points, declared peaks — are emitted as `Marker`s or `HLine`s of
`kind="handle"`. The report draws them as passive markers; on-plot editing
(step 6) attaches to exactly these ids. Without them the figure hook would
have nothing to hook.

**Ids are addresses, stamped by the evaluator.** A mode does not know its
leaf's path (`compare` receives arrays only), so it emits local ids —
`tube/band`, `tube/upper/3`, `points/target/0` — and the evaluator prefixes
each with the leaf's RFC 6902 path when it builds the leaf result. The full
id `<leaf_path>/<local>` is the same address a patch op uses, so a figure
handle and a spec edit share one name. This is the seam on-plot editing
attaches to (§4.6).

**Leaf results are addressed by path, not position.** The evaluator already
carries each leaf's `VariableComparison` on its `MetricResult`
(`diagnostics["variable"]`), and `flatten_evaluation` already derives leaf
paths structurally from the tree. The report nevertheless pairs the flat
comparison list back to leaf paths by `zip` order and drops all per-leaf data
with a warning when the counts differ (`context_builder.py:962-973`). That
positional pairing and its mismatch branch are deleted; the figure view and
the tree view look leaves up by path through `flatten_evaluation`.

**Leaves without a trajectory.** A scalar leaf on a statistics path has no
tracked variable (`variable` is the quantity path). Its view has no
trajectory and its figure is an `HLine` for the value and the bounds. A
`VariableView` may therefore have no trajectory, and the builder renders what
it has.

**The summarize hook.** `MetricDescriptor` gains

```python
summarize: Callable[[VariableComparison], ScoreSummary]
```

returning the score value, its label, the criterion text and the mode-specific
key/value pairs the report shows today. `_extract_mode_values`,
`_leaf_score_display` and the spectrum gate in `context_builder.py` are
deleted (the spectrum is dominant-frequency geometry now); the report's tree
view and the terminal detail printer (`cli/review._print_detail`, which today
prints NRMSE fields for every mode) both read `summarize`. A registered
third-party metric supplies its own; the default summarizes `passed`, `score`
and `tolerance_used`.

### 4.3 The figure layer

`src/dstf/figures/`:

- `view.py` — `VariableView`: name, actual and reference trajectories (either
  may be absent), overlays with role and kind, the leaves that target this
  variable (each with its geometry, window primitive, verdict and summary),
  and the per-variable error series — signed, absolute and per-point NRMSE, on
  the reference grid, computed once here with the comparator's own
  interpolation, all `kind="error"`. Built from a `TestComparison` plus the
  raw trajectories and overlays; both tiers build it the same way. Diagnostic
  and no-baseline trajectories are views with no leaves.
- `plotly.py` — `variable_figure(view, budget) -> dict`. One builder per
  primitive kind in a `TRACE_BUILDERS` registry keyed on the primitive type;
  assembly adds actual and reference traces, overlays styled by role, the
  window band, pass/fail colouring, legend defaults, and a spectrum subplot
  only when a `panel="spectrum"` primitive is present. Builder details the
  current JS gets right and this keeps: an `HLine` inside a `Window` is drawn
  clipped to the window with the outside segments muted; full-height `Box`es
  use paper coordinates; invisible autorange anchors keep bounds in view.
  Every trace and shape carries the primitive `id` in `meta`. The module
  contains no mode name.
- `decimate.py` — LTTB over the arrays of a built figure dict under
  `config.max_embedded_samples`, marking `full_samples` per trace as the
  report does today. A `Band`'s upper and lower are decimated on one shared
  x through the existing `reporting/decimate.decimate_pair`, which moves
  here. The tool uses the same function and budget. The report's payload
  budget test (`tests/test_report_size_budget.py`) stays and now covers bands
  and error series.

The output is a plain dict with `data`, `layout` and `config`. No `plotly.py`
dependency. The report embeds it and renders with the vendored `plotly.min.js`;
the tool renders it with NiceGUI's plotly element, which accepts the same dict.
One figure, two plotly.js builds; the smoke tests in §6 exercise both, and the
versions are aligned when they drift.

The full-resolution `comparison_data.json` sidecar keeps its current shape
(trajectories, not figures). Nothing in the page fetches it, but
`dashboard_render.py` reads it, so its shape is load-bearing and unchanged.

### 4.4 The reporter after the split

**Kept**, each asserted by Playwright before the deletion commit lands:

- per-variable figure rendered from the embedded dict, with the trace count
  the dict declares, and the decimation banner when a trace was decimated;
- zoom and hover, which is how values are read off a plot; legend toggles;
  the overlay picker (overlays are `legendonly` traces; the picker toggles
  visibility);
- the error-overlay toggle (signed, absolute, per-point NRMSE, on the right
  axis);
- the test filter; sweep a selection and copy a command, extended to the
  logged action menu: `run`, `compare`, `compare --accept`, `--rerun`, and
  `dstf author --config <cfg> --filter <selection>`. The command builder
  lives in `dashboard.js`, so the dashboard's script changes by that one
  command; the rest of the dashboard is untouched;
- the metric tree, read-only, with the CLI verdict and `summarize` text per
  leaf, server-rendered;
- statistics, diagnostics and artifact sections; diagnostic and no-baseline
  plots, which become figure dicts too;
- the window shown as a band from the `Window` primitive, open-ended when one
  end is `None`.

**Deleted**: `scorers.js`, `editors.js`, `tree-ui.js`, the patch-export half
of `export.js`, `mode_controls_html` and `window_controls_html` in the report
context, `emit_mode_schemas` and the `MODE_SCHEMAS` map (the report has no
forms; `derive_schema` stays for `export-schema` and the tool's forms), the
JSON-Patch download, live per-leaf scoring, `_JS_SCORED_METRICS`,
`cli_authoritative` and its badge, `_mark_decimated_cli_authoritative`,
`_extract_spectrum` (migrated into dominant-frequency geometry),
`tests/test_scorer_parity.py`, the parity comments in `algorithms.py`
(`:647-653`, `:1113`), the authoring sections of `docs/qa/reporter_checklist.md`.

**Tests that go with them or change shape** (listed so the plan budgets them,
not as a count to hit): `test_interactive_html_snapshot.py` and its
`tests/golden/interactive_*.hash` goldens, `test_interactive_playwright.py`,
`test_interactive_points.py`, `test_interactive_event_timing.py`,
`test_interactive_range_window.py`, `test_interactive_against_baselines.py`,
`test_mode_controls.py`, the `emit_mode_schemas` cases in
`test_export_schema.py`, and the JS-facing cases in `test_comparator.py`,
`test_metric_registry.py`, `test_scalar_metric.py`, `test_report_suite.py`.
The code they covered is deleted with them; the coverage floor is checked at
the end of step 2 and the geometry and figure tests are expected to carry it.

**Documents that change in step 2**: `README.md` (the reporter-as-IDE story),
`docs/vision.md` (the "Reporter as authoring IDE" section is retitled to the
two tiers and its live-preview and parity paragraphs go), `docs/architecture.md`
(the `figures/` and `authoring/` packages; the report render flow),
`docs/extensibility.md` (the capability contract joins the plug-in
contracts), `docs/usage.md`, and `CLAUDE.md`'s overview and tree.

The end state is a predicate, not a count: **no JavaScript under `templates/`
computes a verdict, a bound, a box, a spectrum or an event; every number the
page draws comes from an embedded figure dict or a server-rendered string.**

### 4.5 The Session

`src/dstf/authoring/session.py`. The tool with no UI.

```python
class Session:
    project: Project | None   # Config, discovered TestModels, ReferenceStore, last results
    spec: SpecDocument        # loaded test_spec.json, content hash, pending: list[PatchOp]
    selection: Selection      # tests: set[str], variable: str | None, leaf_path: str | None
    running: RunningJob | None    # the job in flight: label, progress, locked tests
    def open(self, config_path) -> None
    def rescore(self, model_id) -> TestComparison    # comparator against spec + pending
    def figure(self, model_id, variable) -> dict     # VariableView -> variable_figure
    def stage(self, ops: list[PatchOp]) -> None      # then rescore the ops' models
    def save(self) -> None       # spec-update path; refuses if the on-disk hash changed
    def discard(self) -> None
    def export(self) -> dict     # the multi-model envelope (§4.7)
    def apply(self, outcome: Outcome | Sequence[Outcome]) -> None
    def on(self, event: Literal["spec", "results", "job", "selection"], cb) -> None
```

A `PatchOp` carries its `model_id`; pending ops are grouped by model.

**`rescore`** calls the per-test comparator `dstf compare` already uses
(`comparator.compare_test`), with the backend's `read_last_results` and the
recorded-backend rule that `cmd_compare` applies today, extracted into a
function both call. Its test models must come from the spec *with pending ops
applied*, and today spec parsing and discovery read only from disk. New seam,
own commit in step 3: `parse_test_spec` accepts a parsed document, and
discovery accepts a pre-parsed spec; the `.mo` scan merge is unaffected. For a
test whose spec has no explicit tree, `rescore` stages the same implicit-tree
materialization op that `spec-update` prepends today, through the same
function, so the in-memory document and the saved one never differ in shape.
A test with no last results, a failed simulation, or no baseline rescores to
the same `NO_REF` / `SIM_FAIL` states the CLI reports, and its figure is the
empty-state figure (trajectories present or not, no leaves scored).

**`save`** applies exactly the pending ops through the same read-modify-write
path as `spec-update`, then clears them. It refuses if the spec's on-disk
content hash differs from the one taken at load and offers reload.

**Concurrency.** A `Job` runs on a worker thread; the Session is touched only
from the UI loop, and the job posts its completion back to it. While a job is
running, the models in its selection are *locked*: `stage`, `rescore` and
`StoreWrite` on them are refused with the reason, because the run pipeline
clears their simulation directories at start and rewrites the batch manifest.
Every other model stays editable.

`SessionView` is the read-only facade capabilities receive: the same
attributes, `rescore`, `figure`, and nothing that writes.

### 4.6 Capabilities

`src/dstf/authoring/capabilities.py`.

```python
class Scope(Enum): PROJECT, TESTS, VARIABLE, LEAF

@dataclass(frozen=True)
class Patch:        ops: list[PatchOp]
@dataclass(frozen=True)
class StoreWrite:   run: Callable[[ReferenceStore], None]
@dataclass(frozen=True)
class Job:          run: Callable[[JobContext], None]; label: str; reloads_results: bool = True
@dataclass(frozen=True)
class Artifact:     paths: list[Path]; label: str          # something written outside spec and store
@dataclass(frozen=True)
class OpenProject:  config_path: Path
Outcome = Patch | StoreWrite | Job | Artifact | OpenProject | None

class Capability(Protocol):
    name: str
    label: str
    scope: Scope
    def available(self, s: SessionView) -> bool | str    # False, or the reason it is greyed out
    def panel(self, s: SessionView, ui: Panel) -> None   # builds its controls in the shell's slot
    def act(self, s: SessionView, **inputs) -> Outcome | Sequence[Outcome]

def register_capability(cap: Capability) -> Capability   # loud on duplicate name
def registered_capabilities() -> tuple[Capability, ...]  # registration order
```

**Amended in sub-project 3.** The dataclasses above are built keyword-only, and
`Job` and `StoreWrite` each carry `models: frozenset[str]` — the models the
outcome touches, so a job can lock them and a locked model can refuse a store
write. A job's completion is posted through `Session(post=...)`, the UI loop the
shell supplies; with no `post` it runs on the worker thread. `rescore` stages the
implicit-tree materialization from the variables the flat path scored — the
leaves the report shows, in its order, whether or not the spec entry lists them
(a test tracked by its `.mo` annotation has none there, and may have no entry at
all: the op creates it) — and only when that tree scores what the flat path
scored: it stages nothing for a scored variable whose name
is not one the result carries (the flat path's `x[N]` display label), an
override naming an unregistered mode, a string `quantity` in an override (the
flat path refuses it), or a reference-JSON override the spec does not shadow —
each with its negative test. `stage` materializes by the same rule and refuses a
per-leaf op on a test it cannot build a tree for. `spec-update`, with no result
in hand, still materializes from the entry's own list. `SessionView.rescore` is
the one call on the view that writes — it stages that op — so a capability's
`available` must not call it. `open`, `close` and `OpenProject` refuse while
edits are pending or a job runs; `OpenProject` carries the work dir. A
pattern-tracked spec (`"variables": ["tank.*"]`) materializes from the names the
pattern resolved on the day of the edit; the pattern stays and keeps tracking,
and a later match with no leaf is named by the "tracked variables without a
criterion" warning both tiers show (owner ruling 2026-09-09, D126). A completed job reloads results only when it
succeeded; a failed one leaves the verdicts as they were and reports its error.
While a job runs, a reload triggered elsewhere (a store write on a free model)
keeps the locked models' results and verdicts as they were. `SessionView.project`
is a frozen `ProjectView` — config, tests, results and two store readers — not the
`Project`; the store's writers are reachable only through `StoreWrite.run` and
`JobContext`.

Registration goes through the existing single entry-point group
(`dstf.plugins`, `plugins.py`); a plug-in module calls `register_capability`
on import exactly as it calls `register_metric`. Built-in capabilities register
from `authoring/builtin/`.

**The Session applies outcomes; a capability applies nothing.** `Patch` is
staged and the ops' models rescored. `StoreWrite` runs against the store and
results reload. `Job` occupies the running slot, locks its models, and on
completion reloads results and rescores unless `reloads_results` is false.
`Artifact` is recorded and shown as a link; nothing reloads. `OpenProject`
swaps the project. A capability holds a `SessionView` only, so the
enforcement is structural, not a convention. Adding an outcome kind is a
Session change and is meant to be rare; the five cover edit, write, run,
export and switch.

**`Panel`** is a container the capability owns entirely — any NiceGUI
elements, including figures of its own built with `variable_figure` from the
view (a side-by-side comparison against another backend's baseline is a
capability that builds a second store and draws in its own panel). It also
exposes the shell's centre figure through `panel.figure.on(kind, handler)`:
the handler receives the primitive `id` and the Plotly event payload (click,
relayout, selected). The hook exists from v1 and no v1 capability uses it;
on-plot editing is its first client.

**The shell** (`authoring/app.py`, NiceGUI) is fixed and knows no capability
by name. Header: project, backend, pending count, Save, Discard, Export, Quit,
a link to the dashboard. Left: test list with status, filter, multi-select.
Centre: variable picker, the figure, the read-only metric tree; clicking a leaf
selects it. Right: one tab per capability whose `scope` matches the selection
and whose `available` is true; greyed with the reason otherwise. Bottom: the
job panel. With no project open, the shell shows only the PROJECT-scope
capabilities and the CLI hint; in v1 that state is reached only by closing a
project and is where recent-projects and new-library attach later. The shell
subscribes to the four Session events and re-renders the affected region.

**The acceptance test, applied.**

| Capability | Scope | Panel | Outcome | Status |
|---|---|---|---|---|
| Edit criteria | LEAF | form from the mode Config; window; `against` | Patch | v1 |
| Edit tree | TESTS, exactly one selected | add / remove / wrap / combinator kind, k, weights | Patch | v1 |
| Accept | TESTS | confirm list | StoreWrite | v1 |
| Rerun | TESTS | confirm list, backend shown | Job | v1 |
| On-plot editing | LEAF | handlers on `kind="handle"` primitives via the figure hook | Patch | after v1 |
| Annotate a leaf (`info` note) | LEAF | text field | Patch | attaches; nothing else changes |
| Export figures as PNG | TESTS | format picker | Artifact | attaches; nothing else changes |
| Side-by-side against another backend | VARIABLE | own figure in its panel | None | attaches; nothing else changes |
| Recent projects | PROJECT | list of known configs | OpenProject | later |
| New library | PROJECT | wizard writing a testing.json | Artifact, then OpenProject | later |
| Report review | PROJECT | link | None | later |
| Sweep / UQ study | TESTS | — | Job, but its result is not a set of `TestComparison`s, and the run pipeline overwrites last results | strains; see §9 |

The last row is the point: a capability that needs a new *results model* does
not fit the Session, and that is the signal the record asked this design to
surface rather than paper over.

**Executable form.** A conformance test registers a dummy capability through
the plug-in hook with each `Scope` and each `Outcome` kind, and asserts the
shell lists it, greys it with its reason, and routes its outcome, with no file
outside the test changed.

### 4.7 The tool's v1 surface

**Launch.** `dstf author --config testing.json [--filter ...] [--port N]`
starts NiceGUI on localhost and opens a browser tab. One project per process.
Runs until Ctrl-C or Quit; a tab whose server has gone shows NiceGUI's own
disconnected banner.

**Edit criteria.** Mode switch; the form derived from the mode's Config
dataclass by the existing `reporting/ui/mode_controls.derive_schema`, which
gains a NiceGUI emitter (`authoring/forms.py`) and loses its HTML one
(`derive_schema` also feeds `export-schema`, so it stays shared; its home
after the split is a plan decision); window inputs; the `against` selector
over the test's baselines. Each change stages one patch op and rescores the
test, so figure and verdicts update live. `tube_points` is displayed
read-only; edits write the split upper / lower form. A leaf config error shows
inline on its form.

**Edit tree.** The operations the browser tree editor performs today, as
patch ops against the same paths.

**Accept.** The selected tests' current results become baselines through
`ReferenceStore.accept_results`, comparison settings travelling with the
baseline as now. The aborted-run guard (`unfinished_run_models`) that today
lives only in `cmd_compare` moves into a shared accept function that both the
CLI and this capability call, so the tool cannot accept a result the CLI
would refuse. Own commit in step 3.

**Rerun.** The selection runs through the configured backend on a worker
thread. The run pipeline is today reachable only through `cmd_run`, which
resolves the backend with `sys.exit` on failure, prints, clears simulation
directories, opens a browser and renders the live dashboard. Step 5 extracts
a library callable — configuration and tests in, results out, exceptions
instead of exits, no printing, no browser — and `cmd_run` becomes its CLI
wrapper. That extraction is its own commit, before the capability.

Progress comes from the `status.json` the pipeline's `ProgressReporter`
already writes on every state change; the job panel polls it, which is
exactly what the live dashboard does. No log tail: backends print to stdout
and there is no capture seam; the panel shows per-test state and the
backend's failure message from the results. No cancel: none exists in the
backends (§9). Completion reloads results and rescores. Re-comparing without
simulation is `rescore` and needs no job.

**The pending-patch drawer** lists staged ops in readable form, grouped by
model. Save applies and clears; Discard clears and rescores; Export writes an
envelope.

**The envelope becomes multi-model.** `spec-update` today reads
`{"model": ..., "patch": [...]}`, one model per file, which suits a per-test
report page and not a tool that spans tests. The envelope gains the form
`{"patches": [{"model": ..., "patch": [...]}, ...]}`; the tool writes only
that form; `spec-update` reads both, applying each model's ops through the
same path. Old data, not old code: the single-model form is read because
files in that shape exist, and nothing writes it any more.

**Failure handling.** Job failure lands in the job panel with the backend's
message. Save refuses on a changed hash and offers reload. A capability
raising inside `act` is shown in its tab and leaves the Session unchanged.

### 4.8 Dependencies

- `nicegui` as an optional extra `[author]`, so the core install stays light.
  `uv.lock` is updated. One CI job installs the extra so the NiceGUI
  user-fixture tests run; everywhere else they `importorskip`. `dstf author`
  without it exits with the install hint.
- `matplotlib` removed from `[project.dependencies]`.
- No `plotly.py`. The figure layer emits dicts. Whether NiceGUI's dict path
  works without `plotly.py` installed is verified as the first task of step 4;
  if it does not, `plotly.py` joins the `[author]` extra, not the core.

## 5. Build order

Six sub-projects, each its own plan, each shippable and reviewable alone.

1. **Geometry and figures.** `geometry.py`; every mode emits its geometry
   including handles; the evaluator stamps ids and the positional pairing is
   deleted; `Window` from the evaluator; `summarize` on the descriptor
   replacing `_leaf_score_display` (`_extract_mode_values` and the spectrum
   gate survive to step 2, because the forms and the editor subplot they
   feed do); `figures/` with `view`, `plotly`, `decimate`; the report's
   *initial* render comes from embedded figure dicts. The JS editors keep redrawing their own overlays while a leaf is
   being edited, exactly as today, until step 2 removes them; this step
   changes what the page shows on load, not what an edit does. Also here: the
   matplotlib string, tombstone and dependency go.
2. **The cut.** §4.4's deletions, after Playwright asserts §4.4's kept list;
   the tests and documents §4.4 names; the coverage floor checked. Decision
   entry D125 lands here: the split supersedes D66's boundary.
3. **The Session.** `authoring/session.py`, `SessionView`, outcomes, the
   capability protocol and registry, `rescore`, `stage`, `save` with the hash
   check, `discard`, `export`, the lock set. Three seams as their own commits
   first: in-memory spec parsing and discovery; the multi-model envelope with
   `spec-update` reading both; the shared accept function with the
   aborted-run guard. Fully unit-tested, no framework dependency.
4. **The shell, edit criteria, edit tree.** The `[author]` extra, `dstf
   author`, the layout including the project-less state, the form emitter,
   the two capabilities, the dummy capability conformance test, a tool
   section in the QA checklist. First task: verify the NiceGUI dict path.
5. **Accept and rerun.** The run callable extracted from `cmd_run` as its own
   commit; `StoreWrite`, `Job` and `Artifact` wired; the job panel on
   `status.json`; the lock set exercised. End-to-end against
   `examples/python/PythonTestingLib`.
6. **On-plot editing.** The figure hook's first client: tube control points,
   range handles, point and peak markers as one capability or several,
   emitting patch ops. The first real test of §4.6 on something the shell was
   not built around.

The cubic / Hermite key-frame tube work logged in `docs/ideas.md` is
independent of the split and fits after step 1, where the figure layer draws
the new band with no browser work.

**Gates, identical for every step, all runnable without the owner:** full
pytest, `ruff check`, `ruff format --check`, `mypy` bare, the coverage floor;
a fresh-context diff review per task, told which text is a correction and to
verify by execution; a plan-versus-code check at the end of each sub-project.
The real-library gate is `dstf compare --report` against TRANSFORM's stored
last results, which rescores and renders without Dymola. Nothing in steps 1–4
touches simulation. Nothing is pushed.

Done-checks in the plans are predicates over the end state, never counts.

## 6. Testing

**Geometry against verdict** (replaces the parity suite's guarantee, one level
down). Per mode, property tests over generators written to reach the regimes
that would break each claim: relative widths, `tube_min_width`, constant
interpolation, duplicate reference timestamps, windows that cut a band, points
at times the baseline does not sample, open-ended windows. Assertions: a tube
violation at `t` lies outside the `Band` at `t`; a failed point lies outside
its `Box`; every `VLine` matches a detected boundary; every `Marker` peak is
in the paired peaks; `HLine`s equal the configured bounds; every handle's id
resolves to the spec path it edits.

**Figures.** One golden figure dict per mode from a fixed fixture suite,
compared structurally: trace count, types, ids, array lengths, layout keys.
Values are the geometry tests' job, so a golden does not need re-blessing when
a number changes legitimately. The payload budget test stays.

**The report.** Playwright asserts §4.4's kept list on the slimmed page.
Gated by `importorskip` as today.

**The Session.** Pure Python: `rescore` reflects a staged op; `rescore` on a
flat-override test stages the same materialization op `spec-update` would;
`save` applies exactly the pending ops and clears them; `save` refuses on a
changed hash; `discard` restores the loaded verdicts; each `Outcome` kind
reaches its effect and no other; a locked model refuses `stage`, `rescore`
and `StoreWrite` with the reason; `SessionView` exposes no writer;
`spec-update` reads both envelope forms; the shared accept function refuses
an aborted run from both callers.

**The tool.** NiceGUI's pytest `User` fixture drives the UI in-process without
a browser: capability panels, the form emitter, the conformance test, the
project-less state. One Playwright smoke test opens the real page and edits
one tolerance.

**End to end.** Against the Python example library: open, rerun one test,
accept it, assert the reference file exists with LF endings and the results
refreshed; stage an edit on a locked model during the run and assert the
refusal.

**Named as unrun in every handoff:** a rerun through Dymola or OpenModelica
from the tool, and Windows. Owner-run, once, at the end of step 5.

## 7. Acceptance criteria for the whole

1. **The duplication is gone.** The §4.4 predicate holds; `tests/test_scorer_parity.py`
   does not exist; every registered mode has geometry tests; no `if/elif` on a
   mode name remains in `reporting/`.
2. **A capability that does not exist yet attaches without restructuring.**
   The conformance test passes; on-plot editing (step 6) ships as a
   capability with no change to the shell beyond the hook that existed since
   step 4; the three "attaches; nothing else changes" rows of §4.6 hold by
   inspection of the contract.
3. **The report lost nothing it should keep.** Every entry in §4.4's kept
   list has a Playwright assertion that passes on the slimmed page.
4. **No existing CLI behaviour changes for a user who never opens the
   tool**, except that `run -i`'s detail text is now mode-aware and
   `spec-update` also reads the multi-model envelope. `dstf author` is an
   addition. `export-schema` and the JSON-Schema handoff are unchanged.
   Four further changes are visible and intended: every command that
   discovers tests now reads `<reference_root>/test_spec.json` when no
   `test_spec` is configured — the file `add` writes — so a stray one is
   discovered; `spec-update` refuses to materialize a tree for a spec whose
   variables are glob patterns or whose override names an unregistered mode,
   instead of silently writing a `*` or an nrmse leaf; a model-less envelope
   exits 2 as a config error, where it printed and exited 1; and `run
   --accept` applies the same aborted-run refusal `compare --accept` did, which
   a completed run never triggers.
5. **TRANSFORM** `compare --report` renders every test, and its 331 stored
   verdicts are unchanged by steps 1–4.

## 8. Rejected alternatives

- **Keep the JS scorers and fix the parity suite.** Treats the symptom; the
  next mode or grid change reopens it. The owner's ruling was to remove the
  duplication, not guard it.
- **Native desktop (Qt / PySide6).** The report is HTML and stays HTML, so the
  figure is a Plotly dict either way; Qt would embed a web view to draw it, or
  re-render in matplotlib and reintroduce the second renderer. Also fails the
  owner's WSL-to-Windows seam and adds large binary wheels.
- **API-first (FastAPI JSON endpoints, HTMX + Jinja UI).** Smallest
  dependencies and scriptable, but every form is hand-written and on-plot
  editing brings JavaScript event plumbing back. Its strength, a scriptable
  API, is preserved as a capability that can attach later.
- **Panel (HoloViz).** Mature, but Bokeh underneath and a Param model that
  would duplicate the Config dataclasses.
- **Static matplotlib PNGs in the report.** Drops the viewing interactivity
  the owner ruled stays.
- **Write-through saves.** Every edit rewrites a version-controlled file; a
  slip while typing lands in git. Explicit save with a visible pending patch
  keeps the file yours.
- **A progress observer on `ProgressReporter` for the job panel.** It would
  work, but `status.json` already exists, is already polled by the live
  dashboard, and costs nothing. Build the seam when a second consumer needs
  it.

## 9. Open questions and follow-ups

- **Where DSTF ends.** Deliberately undecided. §4.6's last row is the
  instrument: a capability that needs a new results model belongs to a
  sibling tool. If a session finds itself scoping a sweep into the Session,
  that is the moment to stop and ask, not proceed.
- **How a glob-pattern spec materializes** — DECIDED 2026-09-09 (D126): the
  patterns resolve against the result when the tool writes the tree, and every
  tracked variable a tree leaves unscored is a structural warning on both
  tiers. `spec-update`, with no result in hand, still refuses to write a tree
  for a pattern.
- **Cancel.** No cancel path exists in `ProgressReporter`, `run_tests` or the
  persistent worker pool. It is backend work: a cancel token threaded through
  `run_tests` and honoured by the pool's dispatch loop. Not promised by v1.
- **Recent projects and the new-library wizard.** Attach at PROJECT scope.
  Where the recent list is stored (a user-level config file) is not designed
  here.
- **Report review in the tool.** A link in v1. If it ever lands, the HTML
  report's audience narrows to CI, archiving and people without the tool,
  which decides how much polish that page deserves.
- **plotly.js version alignment** between the vendored report asset and
  NiceGUI's bundle: noted, checked by the smoke tests, aligned on drift.
- **NiceGUI without `plotly.py`**: verified in step 4's first task.
- **Cubic / Hermite key-frame tubes**: after step 1, own plan.
