# Plots From the Tree (step U1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A test's plots are derived from its criteria tree — one plot per scored variable or array family, in tree order, on both the report and the authoring tool — with each plot's criteria shown in their place in the tree; the `plots` key of `test_spec.json` holds only a person's extras (same-axis context series on a plot, and unscored "watch" plots); Add series goes; Focus gets a variable switcher; variable labels are cut in the middle.

**Architecture:** A pure `discovery/plots.py::derive_plots(...)` turns (the tree's leaves in depth-first order, the variables' labels, the `plots` extras) into `ResolvedPlot`s — each with a `subject` (one variable, or the members of one array family), `context` series, and `watch: bool`. Both tiers draw from it: `reporting/report_suite.py::plot_page_keys` and the Test page's `PlotCard`. `authoring/layout.py`'s edits become ops on the extras (add/remove context, new/remove watch plot, reset). The criteria rows under a plot are the leaves whose variable is in its subject, in tree order, each with its ancestors as a breadcrumb.

**Tech Stack:** Python 3.10+, NiceGUI 3.16 (`[author]`), plotly (figures layer), pytest + pytest-asyncio (NiceGUI user simulation), Playwright + Chromium, uv.

**Owner rulings this plan implements (2026-09-28; grill + board https://claude.ai/artifact/D1c4eQD6AASiDwAvKPVnpV v1, "A for both"):**
- Q1 b — one test-wide criteria tree; plots are views of it.
- Q10 — one plot per scored variable or family, derived from the criteria. Q19 — a family is an array of one quantity, folded by EXPRESSION (the existing `figures/families.py` rule); anything else gets one plot per variable. (The board drew `cat(1, T, p)[i]` of BranchingDynamicPipes as one family; the code does not and must not fold it — it mixes temperatures and pressures.)
- Q17 — derived plots in tree order (a variable's plot sits where the variable first appears in a depth-first walk of the tree); watch plots after, in the order they were made.
- Q16 — a derived plot has no Remove: it goes when its last criterion goes.
- Q12 — context series: other variables drawn greyed on a plot, same y axis, not scored, added from the Variables drawer and removable (×).
- Q13/Q14 — watch plots: a person can create an unscored plot of a variable ("no criteria yet"), add context to it, and remove it; a watch plot whose variable later gets a criterion becomes that variable's derived plot, keeping its context.
- Q11 c — stacked plots plus Focus with a variable switcher.
- The error overlay stays on the right axis (one subject, one scale). Add series (it did nothing visible) goes; the Variables drawer is how context series and watch plots are made.
- Q20 — variable labels are cut in the MIDDLE (the head says what it is, the tail its index); model ids keep the start cut; full text in the tooltip.
- Reverses D134's layout editing (New plot / Remove plot / Reset layout act on watch plots and context only; no stranded rule — a derived plot cannot lose its subject); changes D133's criteria rows (tree order, with ancestors).

**Not in U1 (later steps, same grill):** U2 — the searchable add-form picker, every file variable and diagnostics offered, metrics narrowed to the variable, a variable tracked because a criterion names it (Q18 b: runs record/store/score it; no spec-update change), editors showing the values in effect, aligned fields. U3 — the Statistics card (collapsed to one line naming changed rows), a missing zero count read as 0. U4 — moving a subtree (drag + "Move to…", auto-wrap in `and`).

**Evidence (read at ed62ef0):**
- `discovery/plots.py`: strict `parse_plots` (`{kind, series:[{var}]}`), `default_plots(tracked)` one plot per tracked name, `tracked_names(scored, result)` the one ordering rule, `resolved_plots(test, tracked, available)` → `ResolvedPlot(index, kind, series, missing)`. Parsed in `spec_parser.py:298-305`; validated after a patch by `spec_update.py:170` `validate_entry_plots`.
- No real spec uses `plots` (TRANSFORM-UnitTests' test_spec.json has `model`, `comparison`, `simulation.timeout` only; none of the four examples' specs). The key's meaning can change with no migration (pre-release; not a TRANSFORM seam).
- `authoring/layout.py`: `current_layout`, `add_series`, `remove_series`, `new_plot`, `remove_plot`, `plots_showing`, `stranded`, `stranded_sentence`, `layout_op` (`add`/`replace` of the whole `/plots`), `reset_op` (`remove /plots`). Callers: `plot_card.py:500-527` (Remove plot, Add series), `variables_drawer.py:476-525` (check / new plot), `test_page.py:540-565` (`stage_layout`, `remove_plot`, `reset_layout`), `tree_card.py:216-231` (Reset layout).
- Report: `report_suite.py:145` `plot_inputs`, `:238` `plot_page_keys` (the one producer of `plots`, `plot_figures`, `plot_series`, `plot_sections`; `tree_here` gives a variable's tree only under its first plot); `templates/interactive.html:327-388`; `figures/plotly.py:915` `plot_figure(views, …)` (families by label, one hue ramp per family; no overlays on multi-series plots).
- Tool: `plot_card.py` `PlotCard` (`self.tracked`, `_leaves(view, variable)` by variable in variable order; the criteria header `+` for `tracked[0]`; the "plot only" line); `test_page.py` `rebuild` (:438), `shape` (:625), focus (:575-613); `variables_drawer.py` checks = series on the selected plot.
- Families: `figures/families.py` `family_of` folds an expression only when it names an array element (`_names_an_element`); `group_families(names, labels)` in first-appearance order; `family_label`.

---

## Rules for every task (the project's build loop)

- Another agent may be alive in the repo: commit ONLY with `git commit -F <your own msg file> -- <explicit paths>`; never `--amend`, never `git stash` (not even in a one-liner), never `git checkout -- <path>` (copy aside, restore bytes). Never push. No `.soul/` record id in any tracked file.
- Every shell command in the FOREGROUND with `cd /mnt/d/Modelica/DynamicSystemsTestingFramework &&`; each under 600 s (the not-playwright suite in two chunks: `tests/test_test_page.py` alone, then the rest with `--ignore`); `uv run --no-sync`, never `uv sync`; assert path variables non-empty before `cp`/`rm`; kill only PIDs you started — never `pkill -f` / `killall`; never touch port 8765; screenshots under the scratchpad, never the repo cwd.
- Test-first: see each new test fail for the stated reason, then implement. Locate code by content.
- A UI change is verified in a real browser and by READING a thumbnail (`python3 ~/.claude/skills/ue-windows-wsl/scripts/render_check.py thumb <img> -o <out>.thumb.jpg`), at 1280 and 900 px. A design choice not written here is not the implementer's: stop and report.
- Gates before each commit: the task's tests, `ruff check`, `ruff format --check`; before a task's last commit also bare `mypy`, the not-playwright suite, and the Playwright files the task touched.

## File map

| File | Change |
|---|---|
| `src/dstf/discovery/plots.py` | T1: the extras schema; `derive_plots`; `ResolvedPlot` gains `subject`, `context`, `watch`. |
| `src/dstf/discovery/spec_parser.py`, `spec_update.py` | T1: parse/validate the new key. |
| `src/dstf/authoring/layout.py`, `session.py` | T2: extras edits; `Session.plots` from `derive_plots`. |
| `src/dstf/figures/plotly.py` | T3: context traces; overlays on a plot with one subject. |
| `src/dstf/reporting/report_suite.py`, `templates/interactive.html`, `report.js` | T4: the report's plots from the tree. |
| `src/dstf/authoring/shell/plot_card.py`, `test_page.py`, `tree_card.py`, `style.py` | T5: the Test page's cards; T7: Focus switcher. |
| `src/dstf/authoring/variable_rows.py`, `shell/variables_drawer.py` | T6: the drawer makes context and watch plots. |
| `src/dstf/authoring/shell/*` (labels) | T8: middle cut. |
| docs | T9. |

---

### Task 1: The extras schema and `derive_plots`

**Files:** `src/dstf/discovery/plots.py`, `spec_parser.py`, `spec_update.py`; Tests `tests/test_plots_spec.py`.

**The new `plots` key** — a list of extras, each one plot's subject and its context:
```json
"plots": [
  {"var": "unitTests.x[6]", "context": ["unitTests.x[10]"]},
  {"var": "pipe2.mediums[1].p"}
]
```
`var` names one variable of the result file (never a glob). An entry whose `var` is scored (some leaf of the tree names it, or a member of its family) decorates that variable's derived plot with `context`; an entry whose `var` is not scored is a watch plot. `context` is a list of names (optional, no duplicates, no glob, not the subject itself). Two entries with the same `var` are refused. `[]` = no extras; absent = no extras; `null` refused. Unknown keys refused (the strict rule `parse_plots` has today).

**`derive_plots(leaves, labels, extras, available=None) -> list[ResolvedPlot]`**: `leaves` = the tree's leaves in depth-first order (their `variable`, skipping string-`quantity` scalar leaves, which name no series); derived subjects = the leaf variables grouped by `group_families(order_of_first_appearance, labels)` — one plot per family (members by index) or per lone variable, ordered by first appearance; each extra whose `var` is a member of a derived subject adds its `context` to that plot; each remaining extra is a watch plot (subject = `[var]`), in list order, after the derived ones. `ResolvedPlot(index, kind="time", subject: tuple[str, ...], context: tuple[str, ...], watch: bool, missing: tuple[str, ...])` — `missing` = subject/context names not in `available` (when given). `series` stays as a property = `subject + context` for readers that draw everything. With no tree (nothing scored, no metrics) the derived plots come from the test's tracked names in `tracked_names` order (today's default) — the report of a SIMULATE_ONLY / NO_REF test keeps one plot per tracked variable.

- [ ] **Step 1: Failing tests** (`tests/test_plots_spec.py`): parse/serialize round trip of the example above; refusals (glob `var`, glob in context, duplicate `var`, context naming the subject, unknown key, `null`); `derive_plots` over `and[nrmse·pipe.T[2], k-of-n[nrmse·a.Q, tube·pipe.T[1]], range·b]` with labels mapping `unitTests.x[i]` → `pipe.T[i]` for two of them: plots `[(pipe.T[1],pipe.T[2]) family, a.Q, b]` in first-appearance order (family placed where pipe.T[2] first appears); an extra on `a.Q` adds context; an extra on an unscored `c` is a watch plot after the derived ones; the BranchingDynamicPipes shape — ten `unitTests.x[i]` with labels `cat(1, pipe2.mediums.T, pipe2.mediums.p)[i]` → TEN plots (no fold); a string-quantity scalar leaf adds no plot; no tree → one plot per tracked name. Run: FAIL.
- [ ] **Step 2: Implement**; `validate_entry_plots` checks the new shape. Delete `default_plots` / `resolved_plots` once Task 2 and 4 no longer call them (delete-as-you-go; this task may keep them until then). Mutants: first-appearance order replaced by label sort; watch plots interleaved; family fold by name instead of label.
- [ ] **Step 3: Commit** "plots: derived from the tree; the key holds extras — context and watch plots".

### Task 2: The Session and the layout edits

**Files:** `src/dstf/authoring/layout.py`, `session.py`; Tests `tests/test_layout.py`, `tests/test_session.py`.

`Session.plots(model_id)` = `derive_plots(tree leaves in DFS order, labels, test.plots extras, available=reads.names)`; `Session.layout` returns the extras list. `layout.py` becomes pure edits of the extras, each returning the new list: `add_context(extras, subject, names)`, `remove_context(extras, subject, name)`, `new_watch(extras, var)`, `remove_watch(extras, var)`; an entry left with no context and a scored subject is dropped (nothing to record); `layout_op` / `reset_op` keep their shape (one op on `/plots`). `stranded` and `stranded_sentence` are deleted (a derived plot cannot lose its subject). A staged remove of a subject's last criterion leaves its extra (if any) as a watch plot — no special code: `derive_plots` does it.

- [ ] **Step 1: Failing tests** for each edit and for `Session.plots` over `_page_project` (A's tree `and[tube·x, range·x, range·y]` → plots `[x, y]`), then staging a context series on x, a watch plot on an untracked file name, removing y's last criterion (y's plot goes; with an extra on y it stays as a watch plot).
- [ ] **Step 2: Implement.** Mutants: `add_context` duplicating a name; an emptied entry kept.
- [ ] **Step 3: Commit** "layout: edits of the plot extras; no stranded rule".

### Task 3: Context traces in the figure

**Files:** `src/dstf/figures/plotly.py`; Tests `tests/test_plot_figure.py`, `tests/test_figures.py`.

`plot_figure(subject_views, context_views=(), …)`: the subject draws as today (one variable → `variable_figure` with overlays and error traces; a family → its hue ramp); each context view draws one grey (`--dstf-muted` token colour from the theme table), thinner line on the SAME y axis, legend name `"<label> · context"`, no reference trace, no criteria geometry, `meta` marking it context. Overlays and the error select apply whenever the SUBJECT is one variable (context does not count).

- [ ] **Step 1: Failing tests** (dict shape): a one-variable subject with one context view keeps its error traces and overlays and adds one context trace on `yaxis` "y"; a family subject with context; the context trace has no hovertemplate text placeholders beyond `%{y}` (Mind 9: a real-mouse hover read is Task 5's Playwright). Run: FAIL.
- [ ] **Step 2: Implement.** Commit "figures: context series draw grey on the subject's axis".

### Task 4: The report's plots from the tree

**Files:** `src/dstf/reporting/report_suite.py`, `templates/interactive.html`, `templates/report.js`; Tests `tests/test_report_suite.py`, `tests/test_interactive_report.py`, `tests/test_report_size_budget.py`.

`plot_inputs` / `plot_page_keys` use `derive_plots` (the tree view's leaves in DFS order). Each plot section: its title (folded subject label), its criteria = the leaves whose variable is in the subject, in tree order, each with its ancestors' kinds as a breadcrumb (`and › k-of-n ›`, the root included), context series listed muted ("context · not scored"), a watch plot's section saying "No criteria yet." The "criteria are under its first plot" rule and the "plot only" lines go (a variable is the subject of exactly one plot). Overlays and the error select apply to every plot whose subject is one variable.

- [ ] **Step 1: Failing tests** over report fixtures: plot order follows the tree; a k-of-n child's row shows `and › k-of-n ›`; a context series renders muted and is not a criterion; a watch plot renders "No criteria yet."; the BranchingDynamicPipes-shaped fixture (ten `cat(...)[i]` labels) yields ten sections. Size budget unchanged or within its limit. Run: FAIL.
- [ ] **Step 2: Implement.** A real-browser LOOK at a generated `interactive.html` (1280 px; thumbnail). Commit "report: plots derived from the tree, criteria in tree order with their ancestors".

### Task 5: The Test page's plot cards

**Files:** `src/dstf/authoring/shell/plot_card.py`, `test_page.py`, `tree_card.py`, `style.py`; Tests `tests/test_test_page.py`, `tests/test_author_test_page_playwright.py`.

`PlotCard` takes a `ResolvedPlot`: title = the folded subject label (middle-cut, Task 8) with its result-file names muted; the criteria rows = the leaves on its subject in TREE order, each row led by a muted breadcrumb of its ancestors' kinds (`and › k-of-n ›`); the criteria header `+` presets the subject's first member (unchanged mechanism); context series in the legend with a `×` (removes that context, one op); a watch card reads "No criteria yet." with a `+ Add a criterion` (the header `+`) and a **Remove plot** button (watch only); derived cards have no Remove (tooltip on its place: "This plot exists because of its criteria."). **Add series is removed.** The page's count line reads "N plots · derived from the criteria[ · K watch]". `shape` includes each plot's `(subject, context, watch)`. Reset layout (tree card) stays: it removes the extras.

- [ ] **Step 1: Failing tests** (user simulation) on `_page_project` and a nested-tree fixture: rows in tree order with breadcrumbs; no `add-series` marker anywhere; Remove only on a watch card; a context `×` stages `replace /plots` without that name; removing a variable's last criterion removes its card (and keeps it as a watch card when it had an extra). Playwright: a real-mouse hover on a context trace reads its name in `.hoverlayer text` (Mind 9); LOOK at 1280 and 900.
- [ ] **Step 2: Implement.** Mutants: rows in variable order; Remove on a derived card. Commit "test page: plot cards are the tree's views — rows in tree order, context, watch plots; Add series goes".

### Task 6: The Variables drawer makes context and watch plots

**Files:** `src/dstf/authoring/variable_rows.py`, `shell/variables_drawer.py`; Tests `tests/test_variable_rows.py`, `tests/test_test_page.py`, Playwright.

The drawer acts on the selected plot: a row's checkbox is "shown as context on the selected plot" (checked when the name is in its context; the selected plot's own subject is checked and disabled with the tooltip "The plot's subject"); `+ new plot` makes a WATCH plot of that variable (disabled with "Already the subject of plot k" when it is a subject). `VarRow.stranded` / `criteria` go. The target line reads "Checkboxes add context to plot k of n".

- [ ] **Step 1: Failing tests** for the check → `add_context` op, uncheck → `remove_context`, `+ new plot` → `new_watch` and the new card selected, the subject row disabled. Implement. Mutant: the check adding to the subject. Commit "variables drawer: context on the selected plot; + new plot makes a watch plot".

### Task 7: Focus with a variable switcher

**Files:** `src/dstf/authoring/shell/test_page.py`; Tests `tests/test_test_page.py`, Playwright.

The focus bar gains a select (`focus-switch`, aria-label "Plot") listing every plot by its folded subject label (watch plots marked "watch"); choosing one focuses that plot (the same `focus(index)` path, closing an editor or row on the card that hides). "Show all N plots" stays.

- [ ] Tests (choosing switches the shown card; an open editor on the old card closes), implement, LOOK, commit "test page: Focus switches between plots".

### Task 8: Variable labels cut in the middle

**Files:** `src/dstf/authoring/shell/style.py`, a small helper (e.g. `shell/widgets.py::middle_cut(label, text)`), its callers (plot card title, criteria row names, Variables drawer rows, the focus switcher); Tests.

A long variable label renders as two spans in one flex row: the head (`overflow:hidden; text-overflow:ellipsis; white-space:nowrap; min-width:0`) and the tail (`flex:none`) — the tail is the text from the last `[` (or the last 12 characters when there is none); the full text is the element's `title`. Model ids keep the start cut (Tests drawer, unchanged).

- [ ] Tests: Playwright at 900 px — a `cat(1, pipe2.mediums.T, pipe2.mediums.p)[3]` label in a narrow title shows its head, an ellipsis and `[3]` (read the visible text by range rects, as the Tests drawer test does); the title attribute is the full label; copy/aria unchanged. Implement; commit "labels: variable labels cut in the middle".

### Task 9: Record it; the gate

- [ ] `docs/decisions.md` **D140 — Plots are derived from the criteria tree (2026-09-28)**: the rulings above; what it reverses (D134 layout editing, the stranded rule; D133 rows); the new `plots` schema (no migration: no spec used the key); what U2-U4 will do. `docs/usage.md` (the "Plot layout" section rewritten; the Test page and drawer paragraphs), CLAUDE.md (the `plots` sentence in "test_spec.json format"; the `dstf author` block), `docs/architecture.md:79`, the roadmap (a U row per step). Full gates; commit.
- [ ] The controller: executing review before Task 1 (archives), a mutation review per part, and the TRANSFORM gate over a copy (absolute-path testing.json; stage, never Save): plot count per test equals its scored variables (no families fold on `cat(...)` expressions; a `pipe.T[1:n]`-shaped test folds), tree-order rows with breadcrumbs on a staged nested tree, a context series and a watch plot staged then Discarded, the report regenerated from the COPY (never `compare` the real work dir) with plots in tree order, thumbnails at 1280 and 900, owner files hashed before and after.
