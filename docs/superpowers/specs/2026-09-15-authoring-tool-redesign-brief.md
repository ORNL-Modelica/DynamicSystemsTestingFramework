# Authoring tool redesign — brief

**Status:** agreed with the owner on 2026-09-15 in a design grill. This is the
input to a design pass (Claude Design artboards) and, after it, an
implementation plan — each in its own session. It is not itself a spec or a
plan: it records what was decided, why, and the facts the decisions rest on.

**Context:** the two-tier split
(`docs/superpowers/specs/2026-09-08-two-tier-architecture-design.md`) is fully
built: a read-only report and `dstf author`, a NiceGUI tool with Edit criteria,
Edit tree, Accept, Rerun and Edit on plot. The owner then used the tool on
real data. This brief turns that feedback into a redesign.

---

## 1. The owner's raw feedback (verbatim)

**Reporting tool**
- Secondary axis label(s) need to be shown when a plot is showing a variable
  for it. Such as the NRMSE. I imagine the second axis could be used potential
  for multiple different scales. If we think that is so, then can we have
  multiple secondary y axis. i think there are standard ways to show that
  (i.e., vertical bars with different numbers and the associated label next to
  eachother)

**Authoring tool**
- clicking a test on the left takes several seconds to load
  - i imagine this is because plots, etc. are created once selected. can this
    be something running/buffering in the background so that it feels
    responsive? need to think through this for fast startup and clicking
    around/filtering tests
- clicking another test while one is selected shows nothing until i deselect
  one test
  - i don't think we need to allow multiple tests to be viewed at the same time
    yet but that would be a nice feature - or we can have multiple tabs so a
    user just opens another tab and can compare that way
- scrolling on test list also scrolls whole page making a terrible experience
  (have to scroll down, select, scroll up)
- we should have a flat (default) view of all the test variables. a collapsed
  selectable option (like you have now) could be an alternative option or we
  jsut do the flat version
- edit tree and edit on plot are grayed out. not clear why.
- edit criteria. not clear how to add additional criteria. the old reporter had
  intuitive +/- type buttons that showed the tree which you edit directly and
  you click the criteria you want to modify to get relavant settings for it and
  immediately can also edit the plot
- save/discard/accept/rerun are not descriptive engouth of what they are doing,
  when they should be used, etc.
- what is difference between close project and quit
- need a DSTF in top left or right (branding - may replace with logo in future).
- can we make it so you can change backend to run from the ui (so i can run
  dymola, modelica, etc.)

Raised during the grill:
- the run/rerun system should be an elegant queue: add tests, monitor the
  queue, run in parallel, cancel or delete queued simulations;
- see results live from an in-flight simulation — the test's variables at
  least, ideally any variable — on the test page or reachable from the queue.

---

## 2. Facts gathered for the grill

### 2.1 The old reporter against the tool (parity)

Measured by building the old per-test page from the commit before the cut
(`2a972c0^`) over a copy of `examples/python/PythonTestingLib`, and the tool at
HEAD over the same data.

**The old per-test page.** One scrolling page: summary and stats, the full
metric tree (open), the patch export, then **one section per variable, all
stacked** — each with its status pill, error-overlay select, overlay picker, a
300 px plot, and **a copy of the tree filtered to that variable directly under
the plot**. Every leaf showed its controls inline (visibility checkbox, pill,
label, score, reset / wrap / remove, the mode's fields, window inputs).
Clicking a leaf highlighted it in both tree copies, opened its editor inside
the leaf and wired the plot gestures; every edit redrew the plot, the pills and
the export text at once. The old QA checklist said live re-scoring was retired;
the code still ran JS scorers for nrmse, points, range, tube and
dominant-frequency (others pinned to the CLI verdict).

**The tool today.** Three columns: test list, centre (variable select, one
plot, tree rows as buttons), capability tabs. One variable at a time.

| Feature | Old page | Tool now |
|---|---|---|
| Add leaf | `+` on a combinator: metric, free-text variable (globs, untracked warning), `against`; `+` under a plot presets the variable | Edit tree "add leaf under": variable and metric selects only |
| Remove / wrap / unwrap / kind / k / weights / threshold | inline controls | Edit tree: parity (remove has no confirm; staged, Discard undoes) |
| Weighted `direction` | inline select | **missing** |
| Reset leaf | `↻` restores params and window | **missing** |
| Per-mode scalar fields | form under each leaf | Edit criteria: parity |
| Scalar `quantity` | editable JSON | **missing** (read-only) — no working scalar leaf can be authored |
| Switch a leaf's metric; change `against` | not in old | new in the tool |
| Window numbers | start/end inputs | parity |
| Window from plot | "Set window from plot" brush | **missing** |
| Tube | width mode, synced/unsynced sides, interpolation, min width, points table, auto-seeded points; Shift+click add, Shift+drag move, Shift+right-click delete | width mode / min width / interpolation in Edit criteria; time/upper/lower table and ring drag in Edit on plot; **no sync, no value conversion, no seeding, no add/delete on the plot** |
| Range | drag min/max lines | parity (scalar bounds new) |
| Points | table with live Match column; Shift+click/drag/right-click; box-edge drag for x-tolerance; value-preserving abs/rel switch | table and target drag (time; value only if explicit); **no Match, no add/delete on the plot, no box-edge drag, no conversion** |
| Snapshot from reference | button | **missing** |
| Events | table, live match, Detect from reference/actual | table only; **Detect and match missing** |
| Peaks | spectrum editor, Shift gestures, Detect peaks (source, window provenance), FFT bin hint | spectrum drag of frequency, table; **Detect, hint, add/remove missing** |
| Live re-scoring | JS on decimated data | Python at full resolution for every metric; leaf verdict only, no per-row readouts |
| Per-leaf overlay visibility | checkbox | legend only |
| Patch export | live textarea, copy, download | pending drawer, Save to `test_spec.json`, Export file; no clipboard copy |
| Overlay picker, error-overlay select | per plot | report: same; tool: legend only, error axis never shown |
| Decimation notice | banner | report: list; tool: **missing** |
| Keyboard (Esc, Enter) | yes | **missing** |
| Test list, multi-select, Accept, Rerun | not on the page | new in the tool |

### 2.2 Where a click's time goes (TRANSFORM, 331 tests)

- **Warm click, one browser tab: about 0.85 s to the plot.** About half is not
  the plot: every click rebuilds and resends all 331 test-list rows — about
  0.2 s on the server and a 0.25 s page patch in the browser (each update frame
  carries ~280 KB beyond the figure). The centre costs 40 ms (150 ms on long
  trajectories, mostly decimation); the tab panels 25–45 ms, including the same
  two JSON files read twice.
- **First click after start: about 5 s** — 3 s building an index of the other
  backends' baselines for overlays (about 568 files read once, then cached) and
  about 1 s loading the plotting library.
- **Each extra browser tab** (including a page opened and never connected, for
  60 s) repeats every region rebuild: about +0.4 s per click.
- **The whole document scrolls** (18,700 px tall); the test list has no scroll
  area of its own, so a click low in the list draws the plot about 10,000 px
  above the viewport.
- **Startup: 52 s** — scanning the library's `.mo` files 30 s, reading results
  16 s, scoring 5 s.

### 2.3 Can a running simulation be read while it runs?

Measured with native Dymola 2026x, omc 1.27 and OMPython in `/tmp`:

| Backend | Mid-run data | How | Delay | Notes |
|---|---|---|---|---|
| Dymola (persistent, batch) | yes | `dsres.mat` grows during the run | ≤ 0.5 s | reading did not slow the run |
| OpenModelica | yes | `.mat` written in 4 KB blocks | 4096 / (8 × stored variables) output points; about one point with `-mat_sync=1` | `-mat_sync=1` cost within noise on the models measured |
| FMPy | yes | `step_finished` callback exposes recorded rows | per output step | about +8 % run time |
| Python | no | `simulate()` returns once | end only | would need a contract change |
| Julia | no (read from code) | one `solve()`, one JSON line | end only | worker protocol could add progress lines without breaking |

DSTF's `.mat` reader trusts the header's column count, which lags the data
until a run ends: it failed most mid-run reads (Dymola 4/53 with 400
variables; OpenModelica 1/64 without `-mat_sync`). A reader that works the
column count out from the file size succeeded on every mid-run read. Running
omc and OMPython directly works on this machine, so the tool's OpenModelica
rerun that stays "queued" (§3.6) is a DSTF-side fault. Not measured: reads on
the `/mnt/d` Windows mount where `testing_output` lives.

### 2.4 Verified by the owner

A Rerun through Dymola from the tool completed twice (OffTimer, 14 s), with no
Dymola process left behind. An OpenModelica Rerun from the tool stayed at
`queued` indefinitely with no message.

---

## 3. Decisions

### 3.1 Direction and order

- **This is part of the October push**, and the goal is to do it right with no
  rework rather than make the tool presentable fast.
- **Design pass first, then one implementation plan against the approved
  design.** The only work before the design is what no layout can change: the
  report's secondary axes (§3.4), the server-side costs that exist under any
  layout (the overlay index on first click, per-click disk re-reads,
  decimation), and a Session that loads progressively (§3.8). Everything
  visible — buttons, the test list, greyed reasons, branding, the scroll — is
  built once, in the redesign.
- **Hold the push** of the current branch until the redesign lands.
- **The design pass and the plan each happen in a fresh session.**

### 3.2 Structure

- **Two pages.** A **Suite page**: the test list (grouped or flat, filters),
  multi-select, explicit batch actions, the simulation queue, the backend.
  A **Test page**: one test, all its variables, all its editing, with a
  collapsible test switcher to move between tests without going back. Clicking
  a test on the Suite page opens its Test page. Why: "what I'm looking at" and
  "what a batch acts on" are different jobs, and each page's list stops paying
  for the other's.
- **Viewing and selecting are separate.** Clicking a test views it; checkboxes
  only collect tests for batch actions and never blank the view. The design
  review should consider whether the multi-test select/run/accept workflow is a
  page of its own, much like the report.
- **The Test page follows the old per-test page, modernised.** A sticky header
  (test status, backend, explicit Save / Discard / Export); the full criteria
  tree, collapsible; then every variable stacked with its plot and that
  variable's criteria directly under it. Clicking a criterion opens its editor
  inline, and the plot above becomes its editing surface. No capability tab
  column.
- **Variables are flat by default.** Each plot renders when scrolled into view;
  an edit redraws only the variables it affects; an optional "focus one
  variable" toggle hides the rest.
- **Capabilities declare where they attach instead of getting a tab** — suite
  (the Suite page), test (the Test page header), variable (a variable section's
  toolbar), criterion (the inline editor and plot gestures). Accept and Rerun
  attach at suite and test; Edit tree becomes the +/−/wrap controls on tree
  nodes; Edit criteria and Edit on plot become one inline criterion editor. The
  tool still knows no capability by name and a new one still attaches without
  restructuring; only placement changes. The spec's §4.6 is amended.
- **Selection state is kept per browser tab**, so opening a test in another tab
  to compare becomes cheap later (not built now).
- **The test list offers every combination** of flat / grouped by package and
  short / full names; the default is grouped with short names.
- **Visual language:** the report's (status pills, colours, typography), so the
  report and the tool read as one product, refined on the design canvas.

### 3.3 Clarity

- **A greyed feature explains itself:** its panel stays reachable and says why
  it is unavailable and what would enable it.
- **Every action button says exactly what it is about to do** — its object, its
  effect, a count, and a one-line tooltip on when to use it (for example "Save
  3 spec edits (2 tests) to test_spec.json", "Rerun 2 selected tests
  (Dymola)"). Pending edits and Save / Discard / Export sit in the header of
  both pages, counted across tests, with the drawer listing them by test.
- **Close project is removed** until recent projects exist; Quit becomes "Stop
  server", confirming when edits are pending or the queue is not empty. Open,
  close and recent projects are to be thought through together later.
- **"DSTF" top-left**, followed by the library name and backend.

### 3.4 The report's secondary axes

One labelled right-hand axis per unit family — "Error [signal units]" for the
signed and absolute error, "NRMSE [–]" for per-point NRMSE — offset side by
side, each shown only while a series on it is visible. The fix lives in the
shared figure layer, so the report and the tool both get it.

### 3.5 Editing: what returns from the old page

In this order:
1. adding and deleting points on the plot, and a window brush;
2. live per-row readouts (Match for points, events and peaks; the tube's
   "% inside");
3. Detect events, Detect peaks, Snapshot from reference;
4. points' tolerance-box edge drag, and abs/rel switches that convert the value;
5. tube upper/lower sync, and auto-seeded control points;
6. reset a criterion;
7. a scalar `quantity` editor;
8. weighted `direction`, Esc to close, remove confirmation, clipboard copy of
   the patch.

Per-leaf visibility checkboxes are not coming back (the legend covers them).

**Plot gestures:** a plain drag moves a handle; Shift+click adds a point to the
open criterion; Shift+right-click deletes the handle under the cursor; a "Set
window" button starts the brush; the criterion editor shows a one-line hint.
Clicking empty plot space is not a Plotly event, so the plan opens with a
feasibility test of a small browser-side listener for it.

### 3.6 Backends, runs and the queue

- **Changing the backend reopens the project on that backend** (its results,
  baselines and runs), refused while edits are pending or a run is in flight.
  **"Open in another backend…"** starts a second tool process on another port
  and opens it in a new browser tab — one project and backend per process.
  A side-by-side comparison view is a later capability.
- **A simulation queue replaces the single Rerun job:** one queue per tool
  process; adding while it runs appends; a test is never queued twice; first
  in, first out; removing an item that has not started is immediate; cancelling
  a running item stops its simulation (feasibility tested first); each result
  is read back and rescored as soon as that test finishes.
- **Parallelism:** the number of workers comes from `testing.json`, adjustable
  for the session on the queue panel.
- **Placement:** a queue panel on the Suite page (queued, running with elapsed
  time, finished, failed; remove or cancel per row) and a compact indicator
  ("3 running · 5 queued") in the header of every page.
- **Editing while queued or running:** a queued test stays fully editable and
  runs with the spec as it stands when it starts. A running test's simulation
  settings (stop time, tolerance, output interval) are locked; its criteria stay
  editable and are rescored when the result arrives.
- **Unsaved edits:** a run uses the spec with pending edits applied, and the
  button says so ("Rerun 2 tests (with 1 unsaved simulation edit)").
- **A run that cannot start fails visibly.** Waiting behind busy workers is
  normal and shows its place ("waiting · 3 ahead"), with no timeout. No worker
  able to take items (none started, all died) within a startup timeout (60 s
  default, configurable) fails the waiting items with the backend's message and
  stops that backend's queue.
- **Finished rows** offer "Open test" and "Accept as baseline".
- **The queue lives in memory**; Stop server confirms when it is not empty.
- **Two processes, one spec file:** Save keeps refusing when the file changed
  on disk and offers Reload.
- **The OpenModelica hang is not debugged separately.** The queue plan's
  acceptance includes "an OpenModelica run from the tool completes or fails
  visibly", which forces its cause to be found there.

### 3.7 Live results from running simulations

- **Designed now, built after the queue.**
- **Where:** a running test's own Test page shows the growing actual trace on
  its normal plots, beside the reference and overlays, with a "running · t = 42 /
  100 s" marker; criteria rows read "waiting for result". A running queue row
  opens that Test page. A monitor page for many runs at once is later.
- **No verdicts until a run finishes.** Provisional verdicts for criteria valid
  on a prefix (a tube or range violation already seen) are a follow-up.
- **"Plot another variable…"** on the Test page lists every variable in the
  result, finished or live, drawn as an extra section without criteria, and can
  promote it to a tracked variable with a criterion in one step.
- **Backends that cannot stream** (Python, Julia) show "running · live view not
  available for this backend", and their plots fill in on completion.
- **One `.mat` reader, upgraded** to work the column count out from the file
  size. It must read every stored result back byte-identical (the 331 TRANSFORM
  results among them), and it also recovers the partial results of a crashed or
  timed-out run. Why one reader: two readers of one format drift, which is the
  duplication the two-tier split removed. OpenModelica's `-mat_sync` becomes an
  explicit run option the tool turns on for runs it shows live; the CLI's
  invocation is unchanged, so no unmeasured flushing cost lands on CLI users.
- **Polling:** only the test whose page is open, only its visible variables,
  about once a second; other runs show progress rows in the queue.
- **To verify in the plan:** the Dymola persistent worker deletes a test's
  directory before each run, so the live reader must tolerate the file
  disappearing; and live reads must be proven on the `/mnt/d` mount.

### 3.8 Startup and responsiveness

- **The page appears within about a second** and shows progress ("scanning
  library… N tests found"). The test list fills in as tests are discovered and
  status badges as results are scored; a test opens as soon as it is scored;
  batch actions unlock when loading finishes; the dashboard link works at once.
- **The library scan is cached** across restarts and reused while the `.mo`
  files have not changed.
- **Targets:** under 0.5 s from click to plot for a typical test, under 2 s for
  the largest.

---

## 4. The design pass

Artboards to draft, for the owner to refine on the canvas:
- the Suite page, with the queue panel (queued / running / finished rows,
  remove and cancel);
- the header queue indicator;
- the Test page, criteria collapsed;
- the Test page with a tube criterion open, plot gestures visible;
- the Test page with a points criterion open;
- the Test page while its test is running (growing trace, running marker,
  criteria waiting for the result);
- "Plot another variable…";
- the startup loading state;
- the backend controls, including "Open in another backend…".

---

## 5. Deferred, recorded

- Open, close and recent projects (to be designed together).
- A monitor page for many simulations at once.
- Side-by-side comparison against another backend.
- Provisional verdicts during a run.
- A standard "live source" a backend can declare in optional fields — a
  tool-specific file (Dymola, OpenModelica) or a generic file/folder to watch —
  added later as a non-breaking extension; Julia progress lines under it.
- Persisting the queue across restarts (for example a temporary file listing
  the queued tests).
- Re-applying a process's pending edits on top of a reloaded spec (merge on
  Reload).
