# The Authoring Tool Redesign — Roadmap

**What this is:** the one program that implements
`docs/superpowers/specs/2026-09-15-authoring-tool-redesign-brief.md` (§3 the
decisions, §3.9 and §3.10 the owner's rulings, §5 deferred) against
`DESIGN.md` and the nine artboards under `docs/design/authoring-tool/`. It is
a plan of plans: seven sub-projects in build order, each with its goal, its
scope, what it settles, its acceptance predicates and its real-library gate.
Each sub-project gets its own bite-sized plan, written in a fresh session when
its turn comes and reviewed fresh-context before any build — the pattern the
two-tier split followed (`2026-09-08-the-cut.md` … `2026-09-14-on-plot-editing.md`,
six plans from one spec). Sub-project 1's plan is
`2026-09-15-the-foundations.md`, written with this roadmap.

**Why plans per sub-project, not one plan:** the brief's own §2 shows that
facts found while building change the next recommendation (a warm click is
0.85 s and half of it is the list; the `.mat` reader trusts a lagging header).
A plan written months ahead of its build would carry code sketches against a
codebase the earlier sub-projects have since changed. Each plan is written
against the tree as it stands when the previous sub-project landed.

**The order, and why:** brief §3.1 fixes what precedes the visible redesign —
only the work no layout can change — and §3.7 fixes live results after the
queue. Between those, the visible work follows the brief's own sequence
(§3.2 structure, §3.5 editing, §3.6 the queue, §3.7 live results, §3.9 themes).

| # | Sub-project | Plan | Depends on | Gate over TRANSFORM |
|---|---|---|---|---|
| 1 | The foundations | `2026-09-15-the-foundations.md` | — | report page's two error axes; warm open and first click timed |
| 2 | The Suite page and the new shell | to write | 1 | 331 rows fill in progressively; batch actions unlock; nothing written |
| 3 | The Test page, inline criteria, the drawer, modular plots | to write | 2 | every variable of a test stacked; one tube edit on the plot; a `plots` key round-trips |
| 4 | Editing returns (brief §3.5, in its order) | to write | 3 | Shift+click adds a point on a real trace; brush sets a window |
| 5 | The simulation queue | to write | 2 (3 for the row → Test page link) | an OpenModelica run from the tool completes or fails visibly |
| 6 | Live results | to write | 5 | a Dymola run's growing trace on `/mnt/d`; 331 stored results read back byte-identical |
| 7 | Themes | to write | 1, 2, 3 | dark theme over the report and the tool; plot template follows |

---

## Shared conventions (every sub-project's plan repeats them)

- Gates before every commit: `uv run ruff check`, `uv run ruff format --check`,
  `uv run mypy` bare, `uv run pytest -q -m "not playwright"` and
  `uv run pytest -q -m playwright`; the browser tests RUN (the venv carries
  `[author]` and Playwright: `uv sync --extra dev --extra fmpy --extra om
  --extra author --locked --inexact`, then `uv pip install pytest-playwright`
  if `import playwright` fails — an `--inexact`-less sync prunes both). While
  agents share the venv, `uv run --no-sync`.
- Commits by explicit path (`git commit -F <msgfile> -- <paths>`), never bare,
  never `--amend`; nothing pushed; the push is held until the redesign lands
  (brief §3.1). An isolated worktree is created FROM `v1.0-prep`, never from
  `origin/main`.
- Never run `dstf run` / `dstf compare` / `dstf author` against a real work
  directory from a task or a review: `compare` wipes the report dirs there.
  The real-library gate is the orchestrator's, and it reads before/after
  listings (spec md5, reference dir, work dir).
- Done-checks are predicates over the end state, never counts or "this grep
  returns nothing".
- Every UI change is verified by the real thing, in this order: a spike of
  the library against the figure the code emits; a gate in a real browser
  over real data; a screenshot READ (as a thumbnail). A gate over TRANSFORM
  counts what the feature DID, not only what stayed unchanged; every
  TRANSFORM leaf is nrmse, so a handle feature stages a tube in memory and
  discards.
- A rename is checked in prose as well as identifiers; a correction gets its
  own review; every guard ships with the state in which it fails.
- `docs/ideas.md` and `.soul/` are not touched by a plan. `docs/decisions.md`
  gains at most one entry per sub-project, named in that plan.

## The seams this program touches (coordinated changes)

- **`test_spec.json` gains an optional `plots` key per test** (ruling §3.10,
  sub-project 3). Additive; absent means one plot per tracked variable; the
  writer never omits it once set. The reference-JSON layout does not change.
- **`testing.json` gains `theme`** (ruling §3.10, sub-project 7) and the queue's
  worker count reads the existing `parallel` (sub-project 5). Both optional.
- **The `dstf author` CLI** keeps `--config`, `--work-dir`, `--port`,
  `--no-browser`, `--filter`; sub-project 2 adds `--backend` selection to the
  page and "Open in another backend…" spawns a second process on another port.

---

## 1. The foundations

**Goal.** Everything the brief §3.1 puts before the redesign, because no
layout changes it: the report's secondary axes (§3.4); the visual system as
tokens both tiers read (DESIGN.md "Colors", "Typography"; the font-stack
correction); the tool-side costs that exist under any layout (the overlay
index on first click, the per-click re-reads, decimation); and a Session that
loads in phases and reports them, with the library scan cached (§3.8).

**Settles.** Where tokens live (Python, rendered to CSS variables; DESIGN.md's
"one file" is a mapping in `reporting/theme.py`); where the scan cache lives
(the user cache dir, never the work dir — `Session.open` still writes nothing
there); that the scan cache cannot validate per file on `/mnt/d` (a stat
sweep over TRANSFORM's 3269 `.mo` files measured 27 s, longer than parsing
them) and so opens from the cache and rescans in the background.

**Acceptance predicates.**
1. The report page and the tool draw signed and absolute error on a right
   axis titled "Error [signal units]" and per-point NRMSE on a second right
   axis titled "NRMSE [–]", each visible exactly while a series on it is
   visible, under both bundled plotly.js builds.
2. Every colour and the type stack on the dashboard, the per-test report and
   the tool come from one token table; the stack ends in a generic
   sans-serif; the plot series keep their literal hex.
3. `Session.open` emits `loading` events per phase and per scored test; the
   first click after open pays nothing for the sibling-backend index; a
   second click on the same test reads nothing from disk.
4. A warm `Session.open` over TRANSFORM does not parse `.mo` files; a source
   edit is picked up by the background rescan without a restart.

**Status (built 2026-09-15; corrections landed and gated 2026-09-16).** Tasks
0–2 and 4–10 landed with their fresh-context reviews' test and wording folds;
the held items landed as
`docs/superpowers/plans/2026-09-16-foundations-corrections.md`, one commit and
one review each: Task 3 with `axes.js` asking the emitter for its own handler
and following `Plotly.react`; the Session's cached bases keyed on the scored
list; the rescan re-reading a changed model with its new TestModel; a
scan-cache write that can fail without failing the open; every scored test
announced and one model's scoring failure leaving the results complete.
Beside them, a pre-existing run-path bug (D128): the run verdict is now
applied in `Backend.read_results`, so `run --merge` / `--rerun` report a
timed-out run as `compare` does. Still open, and not sub-project 1's: the
tool's body keeps Quasar's font stack until sub-project 2's shell applies the
token, and the click's 1.2 s is the current shell's.

Gate over TRANSFORM (2026-09-16, over a copy of the 331-result work dir on
local disk): the report's two error axes follow the select and the legend,
both titles side by side (70 px apart, no overlap), body stack ending in
sans-serif, no page errors; in the tool the legend click on "NRMSE
(per-point)" turns `yaxis4` on (`[False, True]`), and it stays on across a
real `Plotly.react` from a staged tolerance edit (two reacts counted,
discarded after); the manifest's one recorded timeout (SteamWater_HCSG) reads
back `success=False` through the merged read, none read as success. First
HTTP 200 at 46 s cold and 18 s warm — the copy sits on local disk, so the
warm figure is not comparable with the 24 s measured over `/mnt/d`; clicks
2.4 s first, then 1.1–1.4 s, unchanged; `Session.figure` 72 ms then 0 ms; the
rescan over 331 models reports no change and leaves the cache file as it
was. Suite at the corrected HEAD: 2088 passed / 3 skipped, 30 browser tests,
ruff, format and bare mypy clean; the spec's hash, the reference-dir listing,
the real work dir and the real cache unchanged.

## 2. The Suite page and the new shell

**Goal.** Brief §3.2 "two pages" and §3.3, §3.8: the new shell (header per
DESIGN.md; "DSTF", library, backend; the queue indicator's slot; pending count
with Save / Discard / Export; Stop server with its confirmations; the
dashboard link), the Suite page (the test list grouped/flat × short/full,
filters, checkboxes that never blank the view, batch actions that say their
object, effect and count, greyed controls that say why), progressive startup
consuming sub-project 1's `loading` events (page within a second; rows fill
in; a test opens as soon as it is scored), per-browser-tab selection state,
and the capability contract's placement model replacing the tab strip (spec
§4.6 amended in this sub-project). Accept and Rerun attach at suite scope.
The backend controls (§3.6): change backend reopens; "Open in another
backend…" starts a second process. The old `app.py` shell is deleted; the
Test page is a stub route until sub-project 3.

**Settles.** The capability placement contract (`suite` / `test` / `variable`
/ `criterion`) and the conformance test for it; the row model for the list
(what is sent per row, so a click does not resend 331 rows); the page-appears
target and how progress is shown.

**Acceptance predicates.** A click on a row changes the view and no checkbox;
a checked set survives clicks; every batch button's label carries its count
and object; a greyed button carries its sentence; the page renders before the
results are read; a capability the shell has never heard of attaches at each
of the four placements; the old tab strip is gone from the codebase.

**Gate.** TRANSFORM: 331 rows appear progressively; the header's counts match
`dstf compare`'s; nothing written; the page never scrolls sideways; a
screenshot READ.

**Status (built 2026-09-16).** Built as
`docs/superpowers/plans/2026-09-16-suite-page-and-shell.md`, one commit and
one fresh-context review per task. What it settled: selection is the page's,
per browser tab, and reaches a capability through `SessionView(session,
selection)` — the Session has none (D129); `Scope` became `Placement` (suite /
test / variable / criterion) with `placements` and `caption(view)` on the
contract; `Session.open` runs on the shell's thread and every Session event
crosses to the pages through one queue an app-level timer drains; a model
whose scoring raised is announced with `error=True` and `open` still emits
`done`. The row model: rows are built once per test set and a check, a click,
a filter or a rescore updates one element. The old `authoring/app.py` and its
tests are gone; the Test page is a stub with the header, the way back, the
model's verdict and the TEST-placement buttons. **Amended predicate:** VARIABLE
and CRITERION placements are listed by the shell's `placed()` query and drawn
by nobody until sub-project 3; the conformance test covers all four through
`placed()` and the two pages that exist. Out of scope, on purpose: the queue
(sub-project 5 replaces the runs card and fills the header's indicator);
"Save keeps refusing when the file changed on disk" is unchanged from the old
shell.

## 3. The Test page, inline criteria, the Variables drawer, modular plots

**Goal.** Brief §3.2 "the Test page", DESIGN.md "Criteria rows", "The Variables
drawer", "Array families", "Plots are modular", rulings §3.9/§3.10. The sticky
test header; the collapsible full tree with +/−/wrap on nodes (Edit tree's
controls, no tab); plots stacked, each rendered when scrolled into view, an
edit redrawing only affected plots; a variable's criteria rows under its plot
with the inline editor (Edit criteria and Edit on plot merged; the plot above
becomes the editing surface); the test switcher and the Variables drawer
(one open at a time; glob-first filter, literal brackets, regex/case toggles;
checkbox = on the selected plot; "new plot" on hover); arrays folded by index
with a slice field and drawn as one hue ramp; save-time glob expansion into
literal leaves (ruling §3.10); the `plots` key in `test_spec.json` read by
both tiers (the report draws the same plots); "focus one variable". Re-hosts what went with the old shell (sub-project 2): the plot redrawn in place after a drag through the figure hook, a refused drag shown in the editor's slot and snapped back (the two `tests/test_edit_on_plot.py` hook tests), and the figure hook's per-owner clearing on a rebuilt panel.

**Settles.** The `plots` schema and its validation; the series model
(`{"var": ...}` now, expression and unit later); how the report renders a
`plots` layout; the in-place redraw per plot.

**Acceptance predicates.** A test with N tracked variables shows N plots by
default; a `plots` entry with two series on one plot draws both on both
tiers; a folded array row expands to literal leaves on Save; the tree's
+/−/wrap stage the same ops Edit tree staged; a criterion row opens its
editor inline and the plot's handles follow.

**Gate.** TRANSFORM: a test with ≥ 4 variables stacked; a tube staged in
memory, its ring dragged on the plot, then discarded; a `plots` key written to
a COPY of the spec and read back by `compare --report`; a screenshot READ.

## 4. Editing returns

**Goal.** Brief §3.5, in its numbered order, on the inline editor: add/delete
points on the plot and a window brush (opening with the feasibility test of
the empty-plot-click listener, since a click on empty plot space is not a
Plotly event); live per-row readouts; Detect events / peaks, Snapshot from
reference; points' box-edge drag and value-converting abs/rel switches; tube
upper/lower sync and auto-seeded control points; reset; the scalar `quantity`
editor; weighted `direction`, Esc, remove confirmation, clipboard copy. The
plan may stop at any numbered item and record where; each item lands with
its own gate. Re-hosts the old shell's editor behaviours deleted with it (sub-project 2): a panel follows an edit another capability, a Discard, a Save or a Reload staged; the criterion's config error shows above its editor; a capability's error shows in its editor and a later success clears it in place, keeping the field being typed in; the editor a selection lands on is the first enabled one; the tree editor's flow through its controls — a wrap the validator refuses shown in place, add, k-of-n, remove — with the tree rows following each edit (the old `test_edit_tree_wrap_add_remove_and_combinator_edits`).

**Settles.** The gesture contract (plain drag moves; Shift+click adds;
Shift+right-click deletes; "Set window" starts the brush); which readouts are
computed server-side (all — the page computes nothing).

**Gate.** TRANSFORM: a points criterion staged in memory, a point added by
Shift+click on the real trace, a window brushed, then discarded; a screenshot
READ.

## 5. The simulation queue

**Goal.** Brief §3.6: a FIFO queue per tool process replacing the single job;
numbered rows (§3.9); workers from `testing.json`'s `parallel`, adjustable for
the session; append while running, never queued twice, remove before start,
cancel a running item (feasibility first); each result read back and rescored
as its test finishes; edit rules (queued fully editable; running: simulation
settings locked, criteria editable); unsaved edits applied and said so; a run
that cannot start fails visibly with a startup timeout (60 s, configurable);
the queue panel on the Suite page and the header indicator; finished rows
offer Open test and Accept; Stop server confirms when the queue is not empty;
Save keeps refusing on a changed file. **Acceptance includes: an OpenModelica
run from the tool completes or fails visibly** — the hang (brief §2.4) is
found here, not debugged separately.

**Settles.** The Session's queue model (replacing `RunningJob`); how the
pipeline reports per-test completion to the tool (the persistent backends'
shared queue and per-worker dispatch already exist); what "cancel" does per
backend.

**Gate.** Dymola: three tests queued, one removed before start, one
cancelled mid-run, one finishes and rescored; OpenModelica: a run completes
or its failure is shown with the backend's message; nothing left running.

## 6. Live results

**Goal.** Brief §3.7: the running test's Test page shows the growing actual
trace with the "running · t = … / …" marker; criteria rows read "waiting for
result"; the one `.mat` reader upgraded to derive the column count from the
file size (byte-identical on every stored result, the 331 TRANSFORM results
among them; partial results of a crashed run recovered); OpenModelica's
`-mat_sync` as a run option the tool sets for runs it shows live; polling only
the open test's visible variables about once a second; backends that cannot
stream say so; "Add plot / Add series" lists the live result's variables.

**Settles.** The live-read contract a backend declares (tool-specific file
now; the generic "live source" is deferred, §5); the Dymola worker's directory
wipe tolerated by the reader.

**Gate.** Dymola on `/mnt/d`: a long test's trace grows on the page; the 331
results re-read byte-identical by the new reader; a timed-out run's partial
result shown.

## 7. Themes

**Goal.** Rulings §3.9/§3.10: `theme` in `testing.json` naming a bundled
theme (`default`, `dark`); a session switch in the tool; the chrome and the
report's stylesheet follow the token table; one plot layout template per theme
in the figure layer; status fills, role chips and plot series unchanged by the
theme.

**Gate.** The dashboard, a report page and the tool under `dark`, screenshots
READ; the plot's background and grid follow, its series do not.

---

## Deferred (brief §5), not scheduled here

Open / close / recent projects; a monitor page; side-by-side backends;
provisional verdicts; the generic live source; queue persistence; merge on
Reload; other plot kinds (profile, x-y, heat map, 3-D) on the same series
model; a true glob leaf.
