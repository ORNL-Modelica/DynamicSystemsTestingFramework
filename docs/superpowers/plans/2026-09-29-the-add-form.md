# The Add Form (step U2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A criterion can be added on ANY variable of the result file or on a diagnostic, through one searchable picker; a variable becomes tracked because a criterion names it (runs record, store and score it); the metric list follows the variable; a criterion's editor shows the values in effect and its fields line up; the Variables drawer's rows give the label the full width and the rail's drawers can be resized.

**Architecture:** A pure `comparison/tree_spec.py::tree_variables(spec)` names the variables a tree needs (leaves that resolve a variable, diagnostics excluded). Every backend adds them to what it records and extracts, AFTER the UnitTests variables and the `variables` patterns (so stored indices stay put). The add form's variable field becomes a custom picker (the drawers' `filter_box` + `needle.matcher`, filtered server-side, sectioned), never a `ui.select` over every name (TRANSFORM has a test with 76,992). The editor reads the effective values the evaluation already computed (`tolerance_used`) through the tree view.

**Tech Stack:** Python 3.10+, NiceGUI 3.16 (`[author]`), the backends (Dymola, OpenModelica, FMPy, JSON driver for Julia/Python), pytest + pytest-asyncio, Playwright + Chromium, uv.

**Owner rulings this plan implements (grill 2026-09-28, witness entries in the project record; D140 "What U2-U4 will do"):**
- Q8 — one searchable picker in the add form: substring / glob / `.*` / `Aa` (the drawers' rules); sections "On this plot", "Other variables" (every result-file name), "Diagnostics".
- Q2 — the metric list narrows to the variable: a diagnostic offers `scalar` only.
- Q7 + Q18 b — a variable is tracked because a criterion names it: runs record, store and score every criterion variable beside the UnitTests variables and the `variables` patterns; NO spec-update / whitelist change; after adding one, the strip says to rerun (then accept, for a baseline metric). The new variable lands on the plot whose `+` was used (it is that plot's subject or a context series — see Task 5); from the tree card's `+` it gets its own plot (automatic: a scored variable has a derived plot, D140). C2's grey watch `+` goes.
- The owner's feedback point 2 — "shouldn't all these fields be prepopulated with the correct values? … tolerance is floating above the others".
- 2026-09-29 — the Variables drawer's rows put the result-file name and "plot N" on a second line; the rail's drawers can be resized by dragging, the width kept per tab.

**Controller's calls (said to the owner in the report; reversible):**
- (K1) A diagnostic criterion is `{"metric": "scalar", "quantity": "<name>.final"}`, seeded: a count (`EventCounter`, or any diagnostic whose final is an integer) → `tolerance: 0, tolerance_mode: "abs"`; otherwise (`CPUtime`) → `tolerance: 0.1, tolerance_mode: "rel"`. Not auto-wrapped in `warn` (the Statistics card, U3, owns that default).
- (K2) The picker lists at most 200 rows (the drawer's cap), sections in the order On this plot / Other variables / Diagnostics, "Showing 200 of N — narrow the search" when capped.
- (K3) The value in effect shows as the field's hint line under it — `in effect: 0.01 (the test's tolerance)`, the source named (`the criterion` / `the test's tolerance` / `the baseline's tolerance` / `the default`) — never written into the spec unless typed.
- (K4) Every field of the editor row reserves the same bottom line (the hint or empty), so the row lines up.
- (K5) The drawer's resize handle: its right edge; width between 240 px and 60% of the window; default 320; kept in `TabState.drawer_width`.

**Not in U2:** U3 (the Statistics card; a missing zero count read as 0), U4 (moving a subtree).

**Evidence (read at 493d1b7):** the tracked set is UnitTests + patterns everywhere (`result_variables.py:25 compute_needed_variables`, `:54 extract_variables`); OpenModelica filters at simulate time (`openmodelica/mos_generator.py:60 build_variable_filter`, `build_simulate_args :151-159`, both lanes); FMPy filters before simulating (`fmpy/backend.py:350-360 _resolve_requested_outputs`, `"output": requested or None`) and at read (`:292-299`, ignoring UnitTests); the JSON driver filters at read (`common/json_driver.py:272-278`); Dymola records everything (`dymola/backend.py:548-568`); backends receive the `TestModel` at simulate and read time; `storage/migrate_names.py:245-285` assumes indices 1..N are the UnitTests variables in order; `store_reference` stores `result.variables`; the comparator scores `var_results_by_name` from `result.variables`; the add form's names are `tracked_names` only (`edit_tree.py:414-465`, `:803-812`); `Session.view` already falls back to `_file_series` for untracked names (seeding works); `scalar` is the only `accepts_quantity` metric; forms.py always passes a validation dict (NiceGUI reserves the bottom space) while the window numbers and selects don't; the effective tolerance is `vc.tolerance_used` / the leaf diagnostics' `tolerance`, not in the tree view the editor reads; the rail drawer is `width=40|360`, `.dstf-drawer-panel { width: 320px }`, no resize code; NiceGUI's plotly element reflows on a size change.

---

## Rules for every task (the project's build loop)

- Another agent may be alive in the repo: commit ONLY with `git commit -F <your own msg file> -- <explicit paths>`; never `--amend`, never `git stash` (not even in a one-liner), never `git checkout -- <path>`. Never push. No `.soul/` record id in a tracked file.
- FOREGROUND commands with `cd /mnt/d/Modelica/DynamicSystemsTestingFramework &&`; each under 600 s (the not-playwright suite in three chunks: `tests/test_test_page.py` alone and two lists of the rest); `uv run --no-sync`, never `uv sync`; kill only PIDs you started, never `pkill -f`; never port 8765; screenshots under the scratchpad; never `dstf run` / `compare` against a real work dir.
- Test-first; named mutants applied and restored (cmp). A UI change: a real browser, thumbnails read. A design choice not written here: stop and report.
- Gates before each commit: the task's tests, ruff, format; before a task's last commit also bare mypy, the whole not-playwright suite, the Playwright files it touched.

## File map

| File | Change |
|---|---|
| `src/dstf/comparison/tree_spec.py` | T1: `tree_variables(spec, diagnostics)`. |
| `src/dstf/backends/common/result_variables.py`, `openmodelica/mos_generator.py`, `fmpy/backend.py`, `common/json_driver.py` | T1: record and extract the tree's variables. |
| `src/dstf/authoring/situation.py`, `shell/test_page.py` | T2: the strip names new variables to rerun. |
| `src/dstf/authoring/builtin/edit_tree.py`, `shell/picker.py` (new), `comparison/seeds.py` | T3-T4: the picker, the metric list, diagnostic seeds; C2's grey `+` goes. |
| `src/dstf/reporting/context_builder.py`, `builtin/edit_criteria.py`, `authoring/forms.py`, `shell/style.py` | T5: values in effect; aligned fields. |
| `src/dstf/authoring/shell/variables_drawer.py`, `rail.py`, `tabstate.py`, `style.py` | T6: two-line rows; a resizable drawer. |
| docs | T7. |

---

### Task 1: A variable a criterion names is tracked (every backend)

**Files:** `comparison/tree_spec.py`, `backends/common/result_variables.py`, `backends/openmodelica/mos_generator.py`, `backends/fmpy/backend.py`, `backends/common/json_driver.py`; tests `tests/test_metric_tree_spec.py`, `test_result_variables.py`, `test_openmodelica_mos.py`, `test_fmpy_backend.py`, `test_json_driver_backend.py`, `test_python_backend.py`, `test_migrate_variable_names.py`, `test_storage.py`.

`tree_variables(spec, diagnostics=()) -> list[str]`: the unique `leaf.variable` of every leaf where `resolves_a_variable(leaf)`, in tree order, excluding names in `diagnostics` (a diagnostic is scored through a quantity path, never extracted as a trajectory — its stored summary is what `scalar` reads). Each backend's needed set = UnitTests names, then the resolved patterns, then `tree_variables(test.metric_tree_spec, diagnostics)` names not already present (APPENDED — indices of the first two groups unchanged; `migrate_names` keeps working). Dymola/OM `extract_variables`: a fourth pass after patterns (before diagnostics) for tree names, a missing one logged as a warning like a missing UnitTests name. OpenModelica: `build_variable_filter(..., extra_names=UT names + tree names)` on both lanes. FMPy: before simulating, `requested` = resolved patterns ∪ UT names ∪ tree names — and when patterns are empty AND there are tree names, pass the union (not `None`: FMPy's default records outputs only — VERIFY that claim against fmpy's docs/source and say which it is); at read, include UT and tree names. JSON driver: read-side union.

- [ ] **Step 1: Failing tests:** `tree_variables` over a nested tree with a string-quantity scalar, a `{"reduce":"max"}` scalar on `CPUtime` (excluded as a diagnostic), duplicates; `extract_variables` with patterns `["a"]`, UT `x[1]`, tree `b` → indices a/x/b in UT, pattern, tree order and `b` present; OM's filter regex contains the tree name on both lanes; FMPy's `requested` contains it (patterns empty case); JSON driver keeps it; `store_reference` stores it; `migrate_names` over a baseline with an appended tree variable leaves it alone. RED.
- [ ] **Step 2: Implement.** Mutants: tree names prepended (indices shift — a migrate test must fail); diagnostics not excluded; OM persistent lane missing the tree names. Commit "tracking: a variable a criterion names is recorded, stored and scored (every backend)".

### Task 2: The strip says to rerun for new variables

**Files:** `authoring/situation.py`, `shell/test_page.py`; tests `tests/test_situation.py`, `tests/test_test_page.py`.

A staged criterion on a variable the in-memory result lacks scores "not found in the result" until a rerun. `strip_for(..., new_variables: int = 0)`: in EDITS the sentence gains ` · N new variable(s): rerun to score them` and Rerun becomes NEXT for that situation (`EDITS_NEW` situation, or an input the emphasis reads — pick the smaller change and say which); after a rerun, a baseline metric on it reads NO_REF-like for that leaf until accepted — the strip's FAIL/NO_REF sentences are unchanged. `new_variables` = `tree_variables(effective tree) − result.variables names`.

- [ ] Tests (a staged `range` on an untracked file name → the sentence and Rerun filled; nothing new → today's sentence), implement, mutant (count from the saved tree), commit "strip: new variables ask for a rerun".

### Task 3: One searchable picker in the add form

**Files:** `authoring/shell/picker.py` (new), `authoring/builtin/edit_tree.py`; tests `tests/test_test_page.py`, `tests/test_builtin_capabilities.py`, Playwright.

`VariablePicker(ui, names_on_plot, file_names, diagnostics, labels, value, on_change)`: a `filter_box` (substring / glob / `.*` / `Aa`, `needle.matcher`, the budget and the "why" line) over a scrolling list of rows in three sections (K2); each row the label (middle-cut) with the result-file name muted; one chosen row highlighted; keyboard: ArrowUp/Down moves, Enter chooses. The capability builds it through `ui_panel` (no shell import from builtin — check the conformance rules; if the capability may not import `shell`, put the picker's pure row model in `authoring/` and let `Panel` offer a `picker(...)` factory the shell implements — say which). `_add_form` uses it; names: `On this plot` = the plot's subject + context, `Other variables` = `Session.variable_names` minus those minus diagnostics, `Diagnostics` = `config.diagnostic_variables` present in the file (Dymola) — on a backend with no diagnostics the section is absent. C2 (`_UNTRACKED`, the grey `+`) is deleted.

- [ ] Tests: the picker's sections and filtering (glob, regex, bad regex says why, the cap line), choosing a row sets the form's variable, a watch card's `+` is enabled and adds a criterion on its untracked variable (staged op; the strip asks to rerun), `test_a_watch_cards_plus_follows_the_tracked_names` rewritten, Playwright: type a glob, arrow + Enter, Add. LOOK at 1280 and 900. Mutants: filter over the name only (not the label); the cap removed. Commit "add form: one searchable picker over every variable".

### Task 4: The metric list follows the variable; diagnostic seeds

**Files:** `authoring/builtin/edit_tree.py`, `comparison/seeds.py`; tests `tests/test_seeds.py`, `tests/test_builtin_capabilities.py`.

Choosing a diagnostic narrows the Metric select to descriptors with `accepts_quantity` (scalar) and `act("add")` writes K1's leaf (`quantity: "<name>.final"`, the K1 tolerance). Choosing any other variable offers every metric whose `accepted_datasets` includes time series (today's list). The metric select updates when the picker's choice changes.

- [ ] Tests (a diagnostic → only scalar, the staged leaf is K1's shape with the right tolerance for CPUtime vs EventCounter; an ordinary variable → today's list), implement, mutant (reduce-final seed for a diagnostic), commit "add form: the metric list follows the variable; a diagnostic seeds a quantity path".

### Task 5: Values in effect; aligned fields

**Files:** `reporting/context_builder.py` (`_augment_tree_view`), `authoring/builtin/edit_criteria.py`, `authoring/forms.py`, `authoring/shell/style.py`; tests `tests/test_forms.py`, `tests/test_builtin_capabilities.py`, `tests/test_test_page.py`, Playwright.

The tree view's leaf gains `in_effect: {field: {"value": v, "source": s}}` for the fields the evaluation resolved (at least `tolerance` from `tolerance_used`, sourced by comparing against the leaf's own params, the test's `comparison.tolerance`, the reference JSON's, the config/default — the comparator knows which; carry the source through). `render_form(..., in_effect=...)`: a field with no own value shows K3's hint; a field WITH a value shows no hint. Every field of the editor row — mode select, form numbers, window start/end, against select — uses the same bottom-space rule (K4): pass `validation=None` when empty and add `hide-bottom-space` nowhere; instead every field gets `bottom-slots` with the hint or an empty hint so heights match (pick one mechanism, measure in Chromium, say which). `Form.values()` still never writes an in-effect value.

- [ ] Tests: nrmse leaf without a tolerance on a test with `comparison.tolerance: 0.01` → hint "in effect: 0.01 (the test's tolerance)", `values()` has no tolerance; a leaf with its own → no hint; Playwright: every field's input line in the row at the same y (±1 px) at 1280; thumbnail. Mutants: the hint shown for an own value; `values()` writing the in-effect value. Commit "editor: the values in effect are shown; the fields line up".

### Task 6: The drawer: two-line rows, drag to resize

**Files:** `authoring/shell/variables_drawer.py`, `rail.py`, `tabstate.py`, `style.py`; tests `tests/test_test_page.py`, `tests/test_author_test_page_playwright.py`, `tests/test_author_suite_playwright.py` (`.dstf-var-row` at 900 px).

A Variables-drawer row: line 1 — chevron, check, ramp, the label (MiddleCut, the full row width); line 2 (muted, smaller) — the result-file name when shown by the names mode, "plot N", `+ new plot`. The rail drawer gets a drag handle on the panel's right edge (a thin element with `cursor: col-resize`, pointer events in a small JS; on release the width reaches Python once) setting the drawer's `width` prop (RAIL + panel) and the panel's width; K5's bounds; `TabState.drawer_width`; the plots reflow by themselves (NiceGUI's plotly ResizeObserver).

- [ ] Tests: the row's two lines (the label's element is alone on its line); Playwright: drag the handle 200 px right → the panel is 520 px, a plot's `.main-svg` narrows accordingly, the width survives a test switch, a drag past the bounds clamps; the long `cat(...)` label shows ≥ 30 characters at the default width (read visible text by range rects). LOOK at 1280 and 900. Mutants: width not kept; no clamp. Commit "drawers: two-line rows; drag to resize".

### Task 7: Record it; the gate

- [ ] `docs/decisions.md` D142 (the rulings; K1-K5; tracking by criteria per backend and the index rule; the picker; what it reverses — C2), `docs/usage.md` (add form, picker, editor hints, drawer), CLAUDE.md (the `dstf author` block; `variables` / tracking sentence in test_spec.json format), the roadmap (U2 BUILT).
- [ ] The controller: executing reviews before Task 1, mutation review per part, and the TRANSFORM gate over a COPY: stage a criterion on an untracked result variable in a test, rerun it ON THE COPY only if the gate brief allows a simulation (otherwise confirm the extraction change by reading one copied result with the new code) and confirm the stored/extracted set; the picker over the 76,992-name test (filter latency); the editor hints on a real test; the drawer resize; owner files hashed before and after.
