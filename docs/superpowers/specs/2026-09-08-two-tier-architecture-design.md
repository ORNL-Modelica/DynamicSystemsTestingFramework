# Two-tier architecture — Design

**Status**: Design — pending owner review
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

- `src/dstf/reporting/templates/` holds ~6,600 lines. `scorers.js` (1,225),
  `editors.js` (2,420) and `tree-ui.js` (1,441) exist to author; `plots.js`
  (227), `dashboard.js` (581), the viewing half of `export.js` (276) and
  `interactive.html` (443) report. Roughly four fifths of the lines serve
  authoring.
- Three JavaScript registries are keyed on the same mode string
  (`MODE_SCORERS`, `MODE_PLOT_CONTRIBUTIONS`, `MODE_PLOT_EDITORS`), and each
  duplicates Python. Tube bounds exist once in Python
  (`algorithms._tube_bounds`) and three times in JS (scorer inline, editor
  `resolveAllBoundsOnGrid`, plot contribution). Points resolution, FFT and
  peak finding, event detection and windowed NRMSE each exist twice.
- Python ships raw `act` / `ref` arrays only. Every bound, box, marker and
  spectrum the page draws is recomputed client-side; Python computes the same
  values during scoring and discards them.
- Adding a mode touches the three JS registries plus two `if/elif` chains in
  `context_builder.py` (`_extract_mode_values`, `_leaf_score_display`).
  `scalar` never got any of the five and therefore has no drawing.
- The browser scores tubes on `ref_time`; the CLI scores on the union grid.
  The page can show a passing tube the CLI failed.
- The record's earlier framing of "three renderers" (matplotlib in the review
  loop and the report writer, Plotly in the templates) was stale: no
  `import matplotlib` exists under `src/`. There is one renderer and the
  duplication is Python-versus-JavaScript. `matplotlib` is a dead dependency.

The owner's rulings that shape the design: all authoring moves out, and
minimal function is argued *back* to the report later, never the reverse;
the line is mutation, not interactivity; the authoring tier must accommodate
capabilities it was not built around; `tube_points` stays readable, never
writable.

## 3. Scope

### In scope

- Geometry primitives returned by every comparison mode; a summarize hook on
  the metric descriptor replacing the two mode chains.
- A figure layer producing Plotly figure dicts, consumed by both tiers.
- The reporter reduced to viewing; the authoring JavaScript, embedded forms,
  patch download, CLI-authoritative badge and parity suite deleted.
- The authoring package: Session, outcomes, capability protocol and registry,
  the NiceGUI shell, and four v1 capabilities: edit criteria, edit tree,
  accept, rerun.
- `dstf author` CLI command.
- On-plot editing (tube control points, range handles, point and peak
  markers) as a capability attached after v1, exercising the figure event
  hook the shell exposes from v1.

### Out of scope, and must not be precluded

- Report review inside the tool. v1 links to the dashboard; a second
  rendering of the report is not built.
- A recent-projects / known-libraries notion. The `OpenProject` outcome and
  PROJECT scope exist from v1 so it can attach.
- Uncertainty quantification and test sweeps. See §9: the design is expected
  to *inform* where DSTF ends, not to decide it.
- A JSON API over the Session. It is itself a capability and attaches when
  something outside the tool needs it.

### Out of scope, full stop

- Any back-compatibility path for the deleted browser editors. Patches
  exported by the old page still apply through `spec-update`, because the
  patch format does not change.
- Windows-specific work. Nothing here touches simulation.

## 4. Design

### 4.1 The line

A surface either mutates or it does not.

| Operation | Tier |
|---|---|
| zoom, hover, legend and overlay toggles, error overlay | reporting |
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
vocabulary:

| Primitive | Fields | Emitted by |
|---|---|---|
| `Band` | `id, time, lower, upper` | tube (on the union grid it scored) |
| `Box` | `id, t0, t1, y0, y1, passed` | points (checkpoint tolerance box), event-timing (per reference event) |
| `Marker` | `id, x, y, passed` | points (target), dominant-frequency (peaks, on the spectrum panel) |
| `HLine` | `id, value, passed` | range (min / max), scalar (bounds, or the baseline value) |
| `VLine` | `id, time, kind` | event-timing (`ref` / `act` events) |
| `Series` | `id, x, y` | dominant-frequency (reference and actual spectra, on the spectrum panel) |
| `Window` | `id, start, end` | the evaluator, not a mode (the window lives on the leaf) |

`nrmse` emits nothing. Every primitive carries `panel: "time" | "spectrum"`
(default `"time"`). A new primitive kind is an explicit addition to this file
plus one trace builder in §4.3; the vocabulary is deliberately small so that
adding to it is cheaper than smuggling shapes into `Series`.

**Ids are addresses.** `id` is `<leaf_path>/<kind>[/<index>]`, where
`leaf_path` is the RFC 6902 path of the leaf in the spec, the same path a
patch op uses. A figure handle and a spec edit therefore share one address;
this is the seam on-plot editing attaches to (§4.6).

**Leaf results are addressed by path, not position.** Today the evaluator
converts each leaf's `VariableComparison` into a `MetricResult` and the
report pairs the flat comparison list back to leaf paths by `zip` order,
dropping all per-leaf data with a warning when the counts differ. The
evaluator instead stamps every leaf result with its `leaf_path` and carries
the `VariableComparison` on it; the figure view and the tree view look leaves
up by path, and the positional pairing and its mismatch branch are deleted.

**Geometry is tested against the verdict** (§6): a violation reported at `t`
lies outside the `Band` at `t`; a failed point lies outside its `Box`; every
`VLine` coincides with a detected boundary. The mode cannot draw one thing and
score another because both come from the same arrays in the same function.

**The summarize hook.** `MetricDescriptor` gains

```python
summarize: Callable[[VariableComparison], ScoreSummary]
```

returning the score value, its label, the criterion text and the mode-specific
key/value pairs the report shows today. `context_builder._extract_mode_values`
and `_leaf_score_display` are deleted; the report's tree view and the terminal
detail printer (`cli/review._print_detail`, which today prints NRMSE fields for
every mode) both read `summarize`. A registered third-party metric supplies
its own; the default summarizes `passed`, `score` and `tolerance_used`.

### 4.3 The figure layer

`src/dstf/figures/`:

- `view.py` — `VariableView`: name, actual and reference trajectories,
  overlays with role and kind, the leaves that target this variable (each with
  its geometry, window primitive, verdict and summary), and the per-variable
  error series (signed and absolute, on the reference grid, computed once here
  with the comparator's own interpolation). Built from a `TestComparison` plus
  the raw trajectories and overlays; both tiers build it the same way.
- `plotly.py` — `variable_figure(view, budget) -> dict`. One builder per
  primitive kind in a `TRACE_BUILDERS` registry keyed on the primitive type;
  assembly adds actual and reference traces, overlays styled by role, the
  window band, pass/fail colouring, legend defaults, and a spectrum subplot
  only when a `panel="spectrum"` primitive is present. Every trace and shape
  carries the primitive `id` in `meta`. The module contains no mode name.
- `decimate.py` — LTTB over the arrays of a built figure dict under
  `config.max_embedded_samples`, marking `full_samples` per trace as the
  report does today. The tool uses the same function and budget.

The output is a plain dict with `data`, `layout` and `config`. No `plotly.py`
dependency. The report embeds it and renders with the vendored `plotly.min.js`;
the tool renders it with NiceGUI's plotly element, which accepts the same dict.
One figure, two plotly.js builds; the smoke tests in §6 exercise both, and the
versions are aligned when they drift.

The full-resolution `comparison_data.json` sidecar keeps its current shape
(trajectories, not figures) as a handoff artifact. Nothing in the page fetches
it today and nothing will after the split.

### 4.4 The reporter after the split

**Kept**, each asserted by Playwright before the deletion commit lands:

- per-variable figure rendered from the embedded dict, with the trace count
  the dict declares;
- zoom and hover; legend toggles; the overlay picker (overlays are
  `legendonly` traces; the picker toggles visibility);
- the error-overlay toggle (signed / absolute traces on the right axis);
- the test filter; sweep a selection and copy a command, extended to the
  logged action menu: `run`, `compare`, `compare --accept`, `--rerun`, and
  `dstf author --config <cfg> --filter <selection>`;
- the metric tree, read-only, with the CLI verdict and `summarize` text per
  leaf, server-rendered;
- statistics, diagnostics and artifact sections; diagnostic and no-baseline
  plots, which become figure dicts too;
- the window shown as a band from the `Window` primitive;
- the dashboard, unchanged.

**Deleted**: `scorers.js`, `editors.js`, `tree-ui.js`, the patch-export half
of `export.js`, `mode_controls_html` and `window_controls_html` in the report
context and the HTML emitter behind them in `reporting/ui/`, the JSON-Patch
download, live per-leaf scoring, `_JS_SCORED_METRICS`, `cli_authoritative`
and its badge, `_mark_decimated_cli_authoritative`, `tests/test_scorer_parity.py`,
the authoring sections of `docs/qa/reporter_checklist.md`, the vision
document's "Live preview policy" and "Testing asymmetry" paragraphs.

The end state is a predicate, not a count: **no JavaScript under `templates/`
computes a verdict, a bound, a box, a spectrum or an event; every number the
page draws comes from an embedded figure dict or a server-rendered string.**

### 4.5 The Session

`src/dstf/authoring/session.py`. The tool with no UI.

```python
class Session:
    project: Project          # Config, discovered TestModels, ReferenceStore, last results
    spec: SpecDocument        # loaded test_spec.json, content hash, pending: list[PatchOp]
    selection: Selection      # tests: set[str], variable: str | None, leaf_path: str | None
    job: Job | None           # one running action: progress, log tail, cancel

    def open(self, config_path) -> None
    def rescore(self, test_id) -> TestComparison     # comparator against spec + pending
    def figure(self, test_id, variable) -> dict      # VariableView -> variable_figure
    def stage(self, ops: list[PatchOp]) -> None       # then rescore affected tests
    def save(self) -> None       # spec-update path; refuses if the on-disk hash changed
    def discard(self) -> None
    def export(self) -> dict     # the JSON-Patch envelope spec-update accepts
    def apply(self, outcome: Outcome) -> None

    def on(self, event: Literal["spec", "results", "job", "selection"], cb) -> None
```

`rescore` reuses the comparator `dstf compare` runs from last results. `save`
applies exactly the pending ops through the same read-modify-write path as
`spec-update`, then clears them. Loading, discovery, the store and the run
pipeline are the existing ones; if the run pipeline is reachable only through
the CLI command function, extracting a callable is its own commit in step 5.

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
class Job:          run: Callable[[ProgressReporter], None]; label: str
@dataclass(frozen=True)
class OpenProject:  config_path: Path
Outcome = Patch | StoreWrite | Job | OpenProject | None

class Capability(Protocol):
    name: str
    label: str
    scope: Scope
    def available(self, s: SessionView) -> bool | str    # False, or the reason it is greyed out
    def panel(self, s: SessionView, ui: Panel) -> None   # builds its controls in the shell's slot
    def act(self, s: SessionView, **inputs) -> Outcome

def register_capability(cap: Capability) -> Capability   # loud on duplicate name
def registered_capabilities() -> tuple[Capability, ...]  # registration order
```

Registration goes through the existing single entry-point group
(`dstf.plugins`, `plugins.py`); a plug-in module calls `register_capability`
on import exactly as it calls `register_metric`. Built-in capabilities register
from `authoring/builtin/`.

**The Session applies outcomes; a capability applies nothing.** `Patch` is
staged and the affected tests rescored. `StoreWrite` is run against the store
and results are reloaded. `Job` occupies the job slot; the shell streams its
progress; completion reloads results and rescores. `OpenProject` swaps the
project. A capability holds a `SessionView` only, so the enforcement is
structural, not a convention.

**The shell** (`authoring/app.py`, NiceGUI) is fixed and knows no capability
by name. Header: project, backend, pending count, Save, Discard, Export, Quit,
a link to the dashboard. Left: test list with status, filter, multi-select.
Centre: variable picker, the figure, the read-only metric tree; clicking a leaf
selects it. Right: one tab per capability whose `scope` matches the selection
and whose `available` is true; greyed with the reason otherwise. Bottom: the
job panel. The shell subscribes to the four Session events and re-renders the
affected region.

**The figure hook.** `Panel` exposes `figure.on(kind, handler)` where `kind`
is a primitive kind and the handler receives the primitive `id` and the
Plotly event payload (click, relayout, selected). It exists from v1 and no v1
capability uses it; on-plot editing is its first client.

**The acceptance test, applied.**

| Capability | Scope | Panel | Outcome | Status |
|---|---|---|---|---|
| Edit criteria | LEAF | form from the mode Config; window; `against` | Patch | v1 |
| Edit tree | TESTS, exactly one selected | add / remove / wrap / combinator kind, k, weights | Patch | v1 |
| Accept | TESTS | confirm list | StoreWrite | v1 |
| Rerun | TESTS | confirm list, backend shown | Job | v1 |
| On-plot editing | LEAF | handlers via the figure hook | Patch | after v1 |
| Recent projects | PROJECT | list of known configs | OpenProject | later |
| Report review | PROJECT | link | None | later |
| Sweep / UQ study | TESTS | — | Job, but its result is not a set of `TestComparison`s | strains; see §9 |

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
after the split is a plan decision); window
inputs; the `against` selector over the test's baselines. Each change stages
one patch op and rescores the test, so figure and verdicts update live.
`tube_points` is displayed read-only; edits write the split upper / lower
form. A leaf config error shows inline on its form.

**Edit tree.** The operations the browser tree editor performs today, as
patch ops against the same paths.

**Accept.** The selected tests' current results become baselines through the
existing accept path; comparison settings travel with the baseline as now.

**Rerun.** The selection runs through the configured backend on a background
thread using the run pipeline and its `ProgressReporter`; the job panel shows
progress and the log tail and can cancel. Completion reloads results and
rescores. Re-comparing without simulation is `rescore` and needs no job.

**The pending-patch drawer** lists staged ops in readable form. Save applies
and clears; Discard clears and rescores; Export writes the envelope.

**Failure handling.** Job failure lands in the job panel with the backend's
message. Save refuses if the spec's on-disk content hash differs from the one
taken at load, and offers reload. A capability raising inside `act` is shown
in its tab and leaves the Session unchanged.

### 4.8 Dependencies

- `nicegui` as an optional extra `[author]`, so the core install and CI
  matrix stay light. `dstf author` without it exits with the install hint.
- `matplotlib` removed from `[project.dependencies]`.
- No `plotly.py`. The figure layer emits dicts. Whether NiceGUI's dict path
  works without `plotly.py` installed is verified as the first task of step 4;
  if it does not, `plotly.py` joins the `[author]` extra, not the core.

## 5. Build order

Six sub-projects, each its own plan, each shippable and reviewable alone.

1. **Geometry and figures.** `geometry.py`; every mode emits its geometry;
   `Window` from the evaluator; `summarize` on the descriptor and the two mode
   chains deleted; `figures/` with `view`, `plotly`, `decimate`; the report
   embeds figure dicts and `plots.js` renders them. Additive: the JS scorers
   still run, so the page is visibly unchanged while its drawings now come
   from Python. Also here: the matplotlib string, tombstone and dependency go.
2. **The cut.** §4.4's deletions, after Playwright asserts §4.4's kept list.
   Decision entry D125 lands here: the split supersedes D66's boundary.
3. **The Session.** `authoring/session.py`, `SessionView`, outcomes, the
   capability protocol and registry, `rescore`, `stage`, `save` with the hash
   check, `discard`, `export`. Fully unit-tested, no framework dependency.
4. **The shell, edit criteria, edit tree.** The `[author]` extra, `dstf
   author`, the layout, the form emitter, the two capabilities, the dummy
   capability conformance test. First task: verify the NiceGUI dict path.
5. **Accept and rerun.** `StoreWrite` and `Job` wired; the job panel; rerun
   through the run pipeline (extracting a callable from the CLI if needed, as
   its own commit). End-to-end against `examples/python/PythonTestingLib`.
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
at times the baseline does not sample. Assertions: a tube violation at `t`
lies outside the `Band` at `t`; a failed point lies outside its `Box`; every
`VLine` matches a detected boundary; every `Marker` peak is in the paired
peaks; `HLine`s equal the configured bounds.

**Figures.** One golden figure dict per mode from a fixed fixture suite,
compared structurally: trace count, types, ids, array lengths, layout keys.
Values are the geometry tests' job, so a golden does not need re-blessing when
a number changes legitimately.

**The report.** Playwright asserts §4.4's kept list on the slimmed page.
Gated by `importorskip` as today.

**The Session.** Pure Python: `rescore` reflects a staged op; `save` applies
exactly the pending ops and clears them; `save` refuses on a changed hash;
`discard` restores the loaded verdicts; each `Outcome` kind reaches its effect
and no other; `SessionView` exposes no writer.

**The tool.** NiceGUI's pytest `User` fixture drives the UI in-process without
a browser: capability panels, the form emitter, the conformance test. One
Playwright smoke test opens the real page and edits one tolerance.

**End to end.** Against the Python example library: open, rerun one test,
accept it, assert the reference file exists with LF endings and the results
refreshed.

**Named as unrun in every handoff:** a rerun through Dymola or OpenModelica
from the tool, and Windows. Owner-run, once, at the end of step 5.

## 7. Acceptance criteria for the whole

1. **The duplication is gone.** The §4.4 predicate holds; `tests/test_scorer_parity.py`
   does not exist; every registered mode has geometry tests; no `if/elif` on a
   mode name remains in `reporting/`.
2. **A capability that does not exist yet attaches without restructuring.**
   The conformance test passes; on-plot editing (step 6) ships as a
   capability with no change to the shell beyond the hook that existed since
   step 4.
3. **The report lost nothing it should keep.** Every entry in §4.4's kept
   list has a Playwright assertion that passes on the slimmed page.
4. **No existing CLI behaviour changes for a user who never opens the
   tool**, except that `run -i`'s detail text is now mode-aware. `dstf
   author` is an addition.
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

## 9. Open questions and follow-ups

- **Where DSTF ends.** Deliberately undecided. §4.6's last row is the
  instrument: a capability that needs a new results model belongs to a
  sibling tool. If a session finds itself scoping a sweep into the Session,
  that is the moment to stop and ask, not proceed.
- **Recent projects.** Attaches at PROJECT scope with `OpenProject`. Where the
  list is stored (a user-level config file) is not designed here.
- **Report review in the tool.** A link in v1. If it ever lands, the HTML
  report's audience narrows to CI, archiving and people without the tool,
  which decides how much polish that page deserves.
- **plotly.js version alignment** between the vendored report asset and
  NiceGUI's bundle: noted, checked by the smoke tests, aligned on drift.
- **NiceGUI without `plotly.py`**: verified in step 4's first task.
- **Cubic / Hermite key-frame tubes**: after step 1, own plan.
