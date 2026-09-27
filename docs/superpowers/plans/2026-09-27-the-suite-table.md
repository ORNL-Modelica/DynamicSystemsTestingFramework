# The Suite Table (step S2a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The authoring tool's Suite page becomes a sortable, filterable table of tests built from the row model S1 made (`reporting/rows.py`), with the owner's toolbar: count pills that filter by status (and an EDITED pill), typed column filters with `.*` / `Aa` once in the toolbar, group rows by package that collapse, a names toggle, a column chooser, "N selected · Clear", and Rerun / Accept as icon buttons with tooltips; rows repaint live as results arrive and edits are staged; and on a cold library scan the rows appear before the scan finishes.

**Architecture:** A Session-tier function `authoring/suite_rows.py::suite_row(session, model_id, ...)` builds a `TestRow` from what the Session holds (its comparison through S1's `summary_fields`, the result's timing, the test's provenance, its staged edits, a running job) — in process, no sidecar read. The page is an AG Grid Community table (`ui.aggrid`, bundled by NiceGUI 3.16 — AG Grid 34.2; its row grouping is Enterprise, so group rows are emulated, as the spike did) whose columns come from `rows.COLUMNS`; its filtering, sorting, grouping and selection run in the browser through S1's `filters.js` plus a new `suite_grid.js`; Python only sends rows (`applyTransaction`) and receives the selection. Accept and Rerun keep their capabilities and dialogs; `ActionBar` gains an icon form.

**Tech Stack:** Python 3.10+, NiceGUI 3.16 (`[author]`), AG Grid Community 34.2 as NiceGUI bundles it, plain JS (no build step), pytest + pytest-asyncio (NiceGUI user simulation), Playwright + Chromium, uv.

**Owner rulings this plan implements (2026-09-26, board https://claude.ai/artifact/88WhEgGHtSjT3pkiJTJ4Sm v2):** shared row model; the page keeps the name "Suite"; Q1 A — group rows inside the table, collapsible, sorting within each group; Q1a B — a "2 selected · Clear" label, Rerun and Accept as ICON buttons with tooltips (their confirm dialogs stay); no "(Dymola)" in Rerun (the header shows the backend); Q1b A — column filters only, `.*` and `Aa` once in the toolbar applying to every column filter, no separate filter box; typed filters (status by the count pills, numbers by `>1e-4` / `1..10`, text by glob); icons with tooltips for view controls (sort, filter, group, names, columns); an EDITED count pill and an Edits column. The approved Suite artboard (`docs/design/authoring-tool/Main.dc.html`, DESIGN.md "Tables": "Sticky header, sortable columns, a filter row, status tint on the row").

**Evidence (measured 2026-09-26, spike in `/tmp/claude-1000/-mnt-d-Modelica-DynamicSystemsTestingFramework/dc775a61-c854-40f3-8eb0-c9b48224f40b/scratchpad/spike-table/`, `app_b.py` + `ag.js` are the reference implementation):**
- AG Grid Community via `ui.aggrid` met every requirement without Enterprise: group rows emulated with full-width rows + an external filter + `postSortRows`; a floating filter row with our own component; `selectAll: 'filtered'`; `applyTransaction` for one row (24–66 ms, ~190 B); a filter keystroke 22–25 ms, 0 B to the server; 331 rows first drawn in ~1.1 s cold (bundle load) and 2,000 in ~0.8 s.
- **The one hard rule:** NiceGUI's aggrid `_update_method = 'update_grid'` DESTROYS and re-creates the grid on ANY Python-side update of the element (`.venv/.../nicegui/elements/aggrid/aggrid.js:22-50`; the spike's `rebuild.py` lost sort, selection and a live-patched value after `grid.classes(add=…)`). After creation the grid is driven only through `run_grid_method` / `ui.run_javascript`.
- `ui.table` (Quasar) also worked but only through four NiceGUI private APIs, had no grid semantics for screen readers and made sort mouse-only; rejected.
- Session facts (read at 7f0e2c1): `session.comparisons` (`session.py:224`); `project.results[m].statistics["timing"]`; `project.tests[i].field_sources`; `session.spec.pending` (`PatchOp.model_id`), `spec.grouped()`; `session.running.models`; `shell.load.failed_models`; events `spec` / `results` / `job` / `loading` + the shell's `poll` / `opened`, drained by `Shell.tick` every 0.1 s (`shell.py:154-185`); `ActionBar.open(cap)` is public and builds its dialog from `selection_of()` at click time (`actions.py:188-256`); no static-asset route exists in the shell today.
- Startup: rows wait on `session.project` (`shell.py:271-278`), set after `load_scan` + `_merge` (`session.py:277-299`); a scan-cache HIT is one JSON parse, a MISS is a full `scan_source_tests` (~28 s cold over /mnt/d; TRANSFORM measured 40 s to the first row cold, 17 s warm, of which ~10 s is the server start).

---

## Rules for every task (the project's build loop)

- Another agent may be alive in the repo: commit ONLY with `git commit -F <your own msg file> -- <explicit paths>`; never `--amend`, never `git stash`, never `git checkout -- <path>` (copy aside and restore bytes). Never push. No `DSTF-NNN` record id in any tracked file.
- Every shell command in the FOREGROUND with `cd /mnt/d/Modelica/DynamicSystemsTestingFramework &&` in the same line; assert path variables non-empty before `cp`/`rm`; kill only processes you start.
- Test-first: write the test, run it, SEE it fail for the stated reason, then implement.
- A UI change is verified in a real browser over real data and by READING a screenshot thumbnail (`python3 ~/.claude/skills/ue-windows-wsl/scripts/render_check.py thumb <img> -o <out>.thumb.jpg`; never a full-res image). NiceGUI's user simulation cannot see AG Grid rows (they live in the browser): grid behaviour is asserted in Playwright; Python-side logic (rows, transactions sent, selection received) in the user simulation or unit tests.
- Gates before each commit: the task's tests, `uv run ruff check`, `uv run ruff format --check`; before the last also `uv run pytest -m "not playwright" -q`, `uv run pytest -m playwright -q` (two batches if >10 min), bare `uv run mypy`.

## File map

| File | Change |
|---|---|
| `src/dstf/reporting/rows.py` | Task 1: the model column is labelled "Test", the test-key column "Key"; `EDITS` column; `TestRow.waiting`. |
| `src/dstf/authoring/suite_rows.py` (new) | Task 2: `suite_row`, `suite_rows`. |
| `src/dstf/authoring/shell/actions.py` | Task 3: `ActionBar(..., icons=...)`. |
| `src/dstf/authoring/shell/suite_grid.js` (new), `suite_grid.py` (new), `suite.py`, `test_list.py`, `tabstate.py`, `shell.py` | Task 4-5: the grid, its assets, the page, live updates. |
| `src/dstf/authoring/session.py`, `shell.py` | Task 6: rows before the scan. |
| `docs/decisions.md`, `docs/usage.md`, `CLAUDE.md` | Task 7. |

---

### Task 1: The row model's words for both surfaces

**Files:** `src/dstf/reporting/rows.py`, `templates/dashboard.html` (only if a label is hard-coded), tests `tests/test_rows.py`, `tests/test_dashboard_render.py`.

- [ ] **Step 1: Failing tests.** In `tests/test_rows.py`: `{c.key: c.label for c in COLUMNS}["model"] == "Test"` and `["test"] == "Key"` (the model names the test a person reads — the board's "Test" column reads `GenericPipe_Test`; the work-dir key `test_0001` is a key); `EDITS.key == "edits"`, `EDITS.kind == "number"`, `EDITS.text(TestRow(model_id="A", edits=2)) == "2 unsaved"`, `EDITS.text(TestRow(model_id="A")) == ""`, `EDITS.sort(...) == 2 / None`; `EDITS not in COLUMNS` (a report has no staged edits); `TestRow(model_id="A", waiting=True).status_text == "…"` and `.status_class == "queued"` (the tool's "not scored yet"; SIMULATED stays the dashboard's word for a simulated-unscored live row). Update `tests/test_dashboard_render.py`'s header-label assertions. Run: FAIL.
- [ ] **Step 2: Implement.** Relabel the two columns (keys unchanged: the dashboard's saved state, `data-*` and tests key on them); add `EDITS: Column` beside `COLUMNS`; `waiting: bool = False` on `TestRow` with `status_text` / `status_class` giving `"…"` / `"queued"` when `status is None and waiting`. Run: PASS; commit "rows: the model column reads Test; an Edits column for the tool; a row waiting for its verdict".

### Task 2: A Suite row from the Session

**Files:** Create `src/dstf/authoring/suite_rows.py`; Test `tests/test_suite_rows.py` (new). Session tier: no NiceGUI import (`tests/test_authoring_imports.py` blocks it — add the module there if that test lists modules).

- [ ] **Step 1: Failing tests** over the FakeLib fixture the Session tests use (grep `tests/test_session*.py` for how a Session is opened on FakeLib and staged):
  1. a scored PASS test → `status == "PASS"`, `n_vars`, `worst_score`, `ref_id == f"ref_{comp.test_id}"` (None when `test_id` is None — never `"ref_None"`), `field_sources` from the `TestModel`, walls from `project.results[m].statistics["timing"]` when present;
  2. a test with a staged edit → `edits == 1` and its RESCORED verdict (the Session rescored it on `stage`);
  3. a model in `session.running.models` → `status == "RUNNING"`;
  4. a model in the `failed` set passed in → `status == "SIM_FAIL"`, `message` starts "scoring failed";
  5. a model with no comparison yet → `status is None`, `waiting is True`, `status_text == "…"`;
  6. `suite_rows(session, [m1, m2], failed=set())` returns rows in the given order.
  Run: FAIL (no module).
- [ ] **Step 2: Implement:**

```python
def suite_row(session: Session, model_id: str, *, failed: Collection[str] = ()) -> TestRow:
    """One Suite row from what the Session holds — its comparison through
    the row model's `summary_fields` (the numbers the report's sidecar
    carries, computed the same way), the result's timing, the test's
    provenance, its staged edits and a running job. In process: a sidecar
    is the full-resolution context, one JSON parse per test."""
```

Edits: `sum(1 for op in session.spec.pending if op.model_id == model_id)` (count once per call site — `suite_rows` builds a `Counter` once and passes it down; do not scan `pending` per row). Running beats every other state; `failed` next; then the comparison; else waiting. Run: PASS; commit "suite: a Suite row from the Session, through the row model".

### Task 3: `ActionBar` draws icon buttons

**Files:** `src/dstf/authoring/shell/actions.py` (`ActionBar.__init__`, `refresh` ~L151-179); Test `tests/test_actions.py` or wherever `ActionBar` is tested (grep).

- [ ] **Step 1: Failing test** (user simulation): `ActionBar(shell, Placement.SUITE, sel, marker="suite-actions", icons={"rerun": "replay", "accept": "done_all"})` renders each placed capability as an icon button (`props` has `icon=replay`, no text), whose tooltip is the capability's caption (`Rerun 2 selected tests`) when available and its why-sentence when not; clicking it opens the same dialog `open(cap)` opens today. A capability not in `icons` keeps its text button. Run: FAIL.
- [ ] **Step 2: Implement** the `icons` parameter (default None = today's behaviour). The caption is the tooltip; a disabled one keeps its reason in the tooltip (the text form's why-line is under the button; an icon has no room). Remove "(backend)" from `rerun.py`'s caption (the header shows the backend — owner ruling), updating its tests (`test_author_suite_playwright.py:166-173` and any other). Run: PASS; commit "actions: an ActionBar draws its capabilities as icon buttons with tooltips; Rerun's caption drops the backend".

### Task 4: The Suite page is a table

**Files:** Create `src/dstf/authoring/shell/suite_grid.js`, `src/dstf/authoring/shell/suite_grid.py`; Modify `suite.py`, `test_list.py` (the Suite page no longer uses `TestList`; the Tests drawer still does), `tabstate.py`, `shell.py` (`page_setup` or the Suite page loads the two scripts); Tests `tests/test_suite_page.py` (rewritten), `tests/test_author_suite_playwright.py` (rewritten).

**The page, top to bottom** (the board's refined Q1 drawing): the header (unchanged); the progress line; ONE toolbar row — count pills (`PASS n · FAIL n · SIM_FAIL n · NO_REF n · EDITED n`, plus `RUNNING n` while a job runs; zero-count pills shown disabled, as the dashboard does), then icon buttons **group** (`account_tree`, tooltip "Group by package" / "Show flat"), **names** (`label`, "Short names" / "Full names"), **columns** (`view_column`, "Choose columns", opens a menu of checkboxes), then icon toggles **`.*`** ("Regex in every column filter") and **`Aa`** ("Match case in every column filter") with `aria-pressed`; at the right "N selected · Clear" (Clear a small `close` icon, "Clear selection"; the label hidden when nothing is selected), then the ActionBar in icon form (Task 3); then the grid; then the runs card.

**The grid** (`SuiteGrid`, `suite_grid.py`): `ui.aggrid` created ONCE per page with:
- `columnDefs`: a selection column; then `rows.COLUMNS` + `rows.EDITS` as `{field: key, headerName: label, headerTooltip: title, colId: key}`, each cell's text from the row's `text` values and sort from its `sort` values (Python sends each row as `{"id": model_id, "_pkg": package, "_name_short": ..., "_name_full": ..., "<key>": text, "<key>__sort": sort value, "status_class": ...}`); the model column renders `<a href="/test/<quoted id>">name</a>` (short or full per the names toggle; `quote` as `suite.py::_open` does today); the status cell a badge coloured by `status_class` (`var(--dstf-<class>-fill)`), the row tinted `dstf-tint-<class>` (`getRowClass`); visible by default: status, model ("Test"), nrmse, vars, sim, warnings, detail ("Message"), edits; hidden by default: test ("Key"), ref, resolution, worker, elapsed, translate, total.
- `rowSelection: {mode: "multiRow", headerCheckbox: true, selectAll: "filtered", enableClickSelection: false, isRowSelectable: not a group row}`; `getRowId: id`.
- Group rows emulated as the spike did (`ag.js`: full-width rows `{id: "g:"+pkg, _g: true, pkg, count}` inserted before each package's rows; a chevron click collapses; `isExternalFilterPresent` + `doesExternalFilterPass` hide collapsed / flat-mode group rows and apply the typed filters and pills; `postSortRows` keeps each group's rows under its header, sorted within). Group rows are KEYBOARD-collapsible too (Enter / Space on a focused group row — the spike left this unbuilt).
- The floating filter row: one input per column (a `floatingFilterComponent` whose input calls `DstfFilters.compile(kind, q, toggles)`; a `BadFilter` marks the input `bad` with the reason as its `title`, and the filter is ignored); the status column's cell shows the pills' selection read-only. Number filters read the `__sort` value, never the text (S1's rule).
- A no-update guard: `SuiteGrid` fails loudly if Python updates the element after it is created (override NiceGUI's update path so a call after the first render raises `RuntimeError("the Suite grid is driven by run_grid_method; an element update rebuilds it")`) — find NiceGUI 3.16's element update entry point in `.venv` and say which one you override and why.
- Client state (grouped, names, toggles, pills, filters, sort, column visibility) lives in the browser and persists in `localStorage` under `dstf-suite-v1` (a per-viewer convenience; wrap every read/write in try/catch). The SELECTION is sent to Python on `selectionChanged` (`TabState.checked`, per tab — D129) and restored after `gridReady` by id. `TabState` loses `needle`, `status_filter`, `grouped` and `short` (the Suite page no longer reads them; the Tests drawer keeps `tests_needle`).
- `suite_grid.js` and S1's `reporting/templates/filters.js` are served to the page: add ONE static route in `Shell.mount` (`app.add_static_file` for each, or a `/assets/` directory route) and load them with `ui.add_head_html('<script src=...>')` on the Suite page; `filters.js` stays the file the dashboard inlines (one module, two surfaces).

- [ ] **Step 1: Failing Playwright tests** (`tests/test_author_suite_playwright.py`, over FakeLib as today): the table has one row per test and one group row per package; clicking a group chevron hides its rows and Enter on a focused group row does too; the FAIL pill leaves only FAIL rows, EDITED only edited ones; `>1e-5` in Worst NRMSE, `*B*` in Test, `.*` then `^FakeLib\.A$`; a bad regex marks the input and hides nothing; a sort on Worst NRMSE keeps each group's rows under its header, empties last both ways; the names icon switches short/full; the columns menu shows "Key" and hides "Message"; selecting two rows shows "2 selected · Clear", the Rerun and Accept icons' tooltips read "Rerun 2 selected tests" / "Accept 2 results as baselines", clicking Accept opens its dialog, Clear empties the selection; a name click opens `/test/FakeLib.A`; the state (a filter, a pill, grouped off) survives a reload; no horizontal page scroll at 1280 and 900 px. Read one thumbnail of the page at 1280 px and one at 900 px. Rewrite `tests/test_suite_page.py`'s user-simulation tests to what remains Python-side (the rows Python sends, the selection it receives, the icon ActionBar's availability). Run: FAIL.
- [ ] **Step 2: Implement** per the description above; the spike's `app_b.py` / `ag.js` are the reference. Run Step 1: PASS. Mutants to run and report: the guard removed then a Python-side `grid.classes(add=…)` (a test must see sort or selection lost — write it); selection not sent to Python; number filter reading the text; postSortRows removed; collapsed state not persisted.
- [ ] **Step 3: Commit** "suite: the Suite page is a table — pills, typed filters, group rows, a column chooser, icon actions".

### Task 5: Rows repaint live

**Files:** `suite.py` (`handle`), `suite_grid.py` (`patch(rows)`, `add(rows)`, `remove(ids)` → `run_grid_method("applyTransaction", ...)` then the JS regroup/recount); Tests (user simulation for what Python sends; Playwright for what the page shows).

- [ ] **Step 1: Failing tests.** User simulation, capturing the grid's `run_grid_method` / `run_javascript` calls: a `loading` event with phase results for model M sends ONE `update` transaction with M's row (not the whole table); a `results` event for [A, B] sends one transaction with both; a staged edit (`spec` event from `stage`) sends A's row with `edits == 1` and its rescored verdict; a `spec` event that ADDS a model sends an `add`, one that drops a model a `remove`; a job start / finish updates the rows of its models (RUNNING, then their verdicts). Playwright: with a sort, a filter and two rows selected, a rescore of a third row (stage an edit through the Test page, come back) repaints that row and keeps the sort, the filter and the selection; the EDITED pill counts it. Run: FAIL.
- [ ] **Step 2: Implement.** `SuitePage.handle` maps each event to the models it touches and sends their rows; the counts (pills) and group counts are recomputed in JS after every transaction (the spike's `agApply`). Run: PASS; commit "suite: rows repaint live, one transaction per event".

### Task 6: Rows before the scan finishes

**Files:** `src/dstf/authoring/session.py` (`open`), `shell.py` (`visible_tests`, `LoadState`), `suite.py`; Tests `tests/test_session*.py`, `tests/test_suite_page.py`.

- [ ] **Step 1: Failing tests.** With the scan made slow (monkeypatch `scan_source_tests` / `load_scan` to block on an Event), `Session.open` emits a new event `known` with the model ids it can name BEFORE the scan: the spec document's models (`spec_models_from_document`) ∪ the work dir's `batch_manifest.json` model ids, filtered by the config's `--filter` / `--package` the way `visible_tests` filters; the Suite page draws those rows as waiting ("…") while the scan runs; when the scan merges, the `spec` event's set replaces them (rows the scan found added, rows it did not removed). On a scan-cache HIT nothing changes (the cache is one JSON parse). Run: FAIL.
- [ ] **Step 2: Implement.** Emit `known` right after the spec document loads (`session.py` ~L272); the shell keeps the known ids until `session.project` is set. Run: PASS; commit "suite: rows appear before a cold library scan finishes".

### Task 6b: A tool Rerun ends on a scored dashboard

**Files:** `src/dstf/authoring/session.py` (where a finished job's results are reloaded on the UI loop), `src/dstf/authoring/builtin/rerun.py`; Test `tests/test_accept_and_rerun.py`.

S1's review batch (2780dc4) made every run end on a static page; the tool's Rerun renders `render_final(work_dir)` in a `finally` with NO comparisons, so after a tool rerun `work_dir/dashboard.html` reads SIMULATED on its rows (the Session scores them later, on the UI loop). An aborted run stays SIMULATED (nothing was scored).

- [ ] **Step 1: Failing test:** after a tool Rerun finishes and the Session has reloaded and rescored its results, `work_dir/dashboard.html` is static and reads the Session's verdicts (a failing rerun test reads `fail`, the others `pass`), not `pending`.
- [ ] **Step 2: Implement:** once the job's results are reloaded (the place the Session emits `results` for the job's models), render `render_final(work_dir, comparisons=list(self.comparisons.values()))` — off the UI loop if it is slow (measure it over the FakeLib fixture and say). Keep the `finally` render in `rerun.py` for a job that fails before it reloads. Commit "rerun: a tool rerun ends on a dashboard with the Session's verdicts".

### Task 7: Record it; the gate

- [ ] `docs/decisions.md` D137 — "The Suite page is an AG Grid table over the row model (2026-09-27)": the rulings, why AG Grid Community (the spike's numbers; Enterprise-only grouping emulated), the no-update rule and its guard, where client state lives (browser, `dstf-suite-v1`) and where the selection lives (per tab, Python), the known-before-scan rows. `docs/usage.md` (the author section): the Suite table's controls. `CLAUDE.md`: the shell line gains `suite_grid.py` / `suite_grid.js` and `authoring/suite_rows.py`. Full gates; commit.
- [ ] The controller runs an executing review of this plan BEFORE Task 1 (building every task in an archive), a mutation review per task or batch, and the TRANSFORM gate over a COPY: 331 rows and 330 PASS + 1 SIM_FAIL in the grid; time to first row cold (separate `XDG_CACHE_HOME`) and warm; S1's dashboard over the copy (cell text identical to before except the pill words and the two relabelled headers); a staged edit → the EDITED pill 1 and the row's Edits cell; a real-mouse sort / filter / collapse; one thumbnail read at 1280 and 900 px; owner files hashed before and after.
