# Form Row and Legend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the Test page of `dstf author`, a tree node's add / wrap / edit forms open as a ROW under the node's row, not as a menu over it; and a plot's legend holds its SIGNALS only, above the plot area, while each criterion's drawing is shown and hidden by a swatch on its criteria row and the three error series are one select on the card's toolbar.

**Owner rulings this plan builds on (2026-09-21 board, `form-row-legend-board`; not reopened):**
- **Form row, 1A–1D, "Yes build that."** The form opens below the node's row, indented like the criterion editor, with the editor's rule; the row stays lit; the title names the node; only the chosen combinator kind's fields show; Cancel beside the action; one sentence under the fields says what the action does to the tree; Unwrap lives inside Edit; one thing is open at a time on the page, so a form replaces an open criterion editor (and the editor replaces a form); on a criteria row, clicking the row's name closes a form open under it. After Add, the new criterion's editor opens under its plot. Under a `warn` or `weighted` parent, `+` is grey with the reason as its tooltip.
- **Legend b.** Signals in the plot's legend, laid ABOVE the plot area; each criterion's drawing toggled by a swatch on its criteria row; the three error traces in ONE select on the card toolbar. The show/hide default is a property of the metric: tube, points, event-timing and dominant-frequency ON; range and scalar OFF; an open criterion always draws; a failing criterion draws by default. The report keeps its criterion legend entries for now; its legend moves above the plot area too (one layout dict, the board's table).

**Owner ruling, 2026-09-24 (not reopened): a failing criterion's swatch CAN hide its drawing.** The OPEN criterion cannot be hidden: it is the editing surface, and its swatch is disabled and says so. A FAILING criterion is drawn by default, and its swatch hides and shows it like any other. Why: the failure stays visible without the drawing — the criteria row's FAIL pill beside the swatch, the card's pill, the closed tree line's tally and jump, the test's header badge, the Suite page — and a swatch that works on one row and is grey on the next, by a verdict that flips on every rerun, would read as broken. `drawn()` in Task 4 is written to this ruling.

**Spike (2026-09-24, Chromium, NiceGUI 3.16's bundled plotly.js, over `Session.plot_figure(A, 0, editing=(TUBE,))` of `tests/test_author_test_page_playwright.py::_page_project`; `Plotly.react` replayed exactly as `ui.plotly.update_figure` sends it, `editrevision` bumped each time). Facts this plan relies on:**
1. A zoom (`xaxis.range`/`yaxis.range` set by relayout) SURVIVES a `react` that sets `visible: false` on a leaf's traces and `visible: false` on its layout shapes, and one that sets them back — five reacts in a row, ranges unchanged to the digit.
2. `visible: false` on a trace removes it; `visible: false` on a SHAPE removes it from `g.shapelayer` (the drawn shape paths went 8 → 4 when the range's two lines were hidden). Hidden shapes STAY in `layout.shapes`, so shape indices do not move.
3. With the range leaf's two lines hidden (shapes 0–1), a real-mouse drag of a tube ring still emits `plotly_relayout` with `shapes[2].xanchor/yanchor` and `shapes[3]…` — the index of the ring in the FULL array, which is what `FigureHook` resolves names by. A ring outside the zoomed x-range is not grabbable (expected).
4. A signal hidden by a real legend click (`Reference` → `legendonly`) STAYS hidden across every later server `react` (the server's `visible` for it never changed, so `uirevision` keeps the user's).
5. Setting the chosen error trace `visible: true` AND its axis (`yaxis3` for signed/abs, `yaxis4` for NRMSE) `visible: true` in the figure shows exactly that axis; the other stays hidden. No client script is needed when the server picks the error series.
6. `legend: {orientation: "h", x: 0, xanchor: "left", y: 1, yanchor: "bottom"}` puts the legend ABOVE the plot area: Plotly grew the top margin by itself (25 → 41 px on a 320 px plot); the legend's bottom edge (41 px) meets the plot's top (41 px). The screenshot was read: no entry covers the trace start or a ring.
7. NOT spiked, established by reading (`edit_tree.py::_lazy_menu`, the 2026-09-20 plan's Corrections item 8): the forms are built when OPENED, from the tree as it is then, and cleared when closed. An inline row keeps that fact if the row's content is built by the icon's own click (under the user simulation a button click DOES run `on_click`; it is a MENU's value a click never moves) and cleared on Cancel / close. What the menu gave for free and the row must add: a form that stays open while an edit is staged ELSEWHERE (Discard, a drag, another node's control) is rebuilt from the tree as it is then — the criterion editor's `_EDITED` rule, applied to the node.

**Found while planning (a regression, fixed by this plan's structure):** the tool's page stopped loading `templates/axes.js` when the old shell was deleted (22d3284); 39363f4 had added it so a legend click on an error trace shows its axis. Today in `dstf author` an error trace clicked on in the legend draws against a hidden axis. After this plan the tool's error traces are not in the legend and the figure itself says which axis is visible (fact 5), so the tool needs no axis script; the prose that says the tool loads it (`axes.js` header, `report_suite.axes_js_source` docstring, `figures/plotly.py::_layout` comment) is corrected in Task 8, and `axes_js_source` is deleted if nothing but a test calls it.

**Architecture — four moves.**
1. **The figure layer draws what the page chose.** `figures/plotly.py` gains `Visibility(hidden: frozenset[str], error: str | None)`. `variable_figure` / `plot_figure` take `visibility: Visibility | None = None`. With one (the tool), every leaf trace is out of the legend, a hidden leaf's traces and shapes are `visible: false`, error traces are out of the legend and only the chosen one is visible, with its axis. Without one (the report), nothing changes but the legend's place. Every leaf trace's `meta` carries `leaf: <path>` on both tiers.
2. **The default is the metric's.** `MetricDescriptor.drawn_by_default: bool = True`; the `range` and `scalar` builtins register `False`. A third-party metric sets it where it registers.
3. **The card owns the choices.** `PlotCard` keeps `choices: dict[leaf_path, bool]` and `error: str | None`; `drawn(leaf, choice, is_open)` decides each leaf; the `Visibility` rides into `Session.plot_figure(…, visibility=…)` and into the signature, so a swatch click or a select change is ONE `update_figure`. A swatch per criteria row; one error select per card whose figure has error traces.
4. **A panel can open a row below its host's row.** `Panel` gains `row(title, build)` and `reveal(leaf_path)`. The Test page owns "one thing open at a time": a form row or the criterion editor. `InlineHost` takes a `row=` target the page builds for each tree row and each criteria row; `ActionBar`'s dialog draws a row inline in its own slot; the test host double does the same. `EditTree`'s cluster opens its forms through `row`, with per-kind fields, a sentence, Cancel, Unwrap inside Edit.

**Seams:** none of the pre-release block's surfaces moves (CLI, `testing.json`, `test_spec.json`, reference-JSON). The report's legend moves above its plots; its entries do not change.

**Not built here:** the report's criteria entries leaving its legend (a later change, the board's table); the "Add a criterion for x" footer (it stays a dialog; not on the board); plan 3c; any change to what a metric draws.

**Review provenance.** The plan text had a fresh-context EXECUTING review on 2026-09-24 (ACCEPT-WITH-FIXES: 2 HIGH, 6 MED, 8 LOW). Tasks 1–7 were built in a `git archive` copy of 631052f, plus the ruling's test from d9250a2. Every red state was run and every named mutation applied. The archive ended at 2719 passed / 18 skipped (not playwright; an archive skips 18 where the tree skips 3), 36 passed (playwright), and `ruff`, `format` and bare `mypy` clean. Every finding is in the block below, which wins where a task's text disagrees. NOT executed by that review: red-on-unchanged-code for the tests it wrote after GREEN (the failing-criterion, windowed-shape, wrap-row and discard page tests, and the new browser tests), a full-suite run per task, and Tasks 8–9. A working reference exists at `<scratchpad>/review-frl.keep.diff` (the archive against 631052f). An implementer may read its hunks for the task in hand. The plan, not the diff, is the specification, and every task still gets its own mutation-running review.

## Corrections from the executing review (binding on every task; where a task's text disagrees, this block wins)

1. **HIGH: hovers are read from `.hoverlayer text`, not `.hoverlayer .hovertext`.** The figure's `hovermode` is `x unified`, which draws one legend-shaped box with no `.hovertext`; the existing hover tests already read `.hoverlayer text` (`HOVER_LINES` in the browser module). The tube's marker traces carry `hoverinfo: "x+y"`, so a hover lists their VALUES, not "Tube upper points". Task 4's browser hover step becomes: a real-mouse hover at t = 0.5 reads `.hoverlayer text`; with the tube drawn it includes `0.6000` and `0.3000` (its handles). Close the tube's editor (an OPEN criterion cannot be hidden), click the tube's swatch, hover again: both are gone. Task 7's hover step reads the same selector. The same selector note goes in every brief's LOOK AND HOVER rule.
2. **HIGH: `_page_project` moves to `tests/test_session.py`.** It lives in the playwright module, which calls `importorskip("playwright.sync_api")`. CI has no playwright, so importing it from `test_test_page.py` or `test_session.py` would silently skip the whole importing module in CI while staying green locally. Move it into `tests/test_session.py` beside `_two_var_project`. Import `TREE` from `test_handles` INSIDE the function, because `test_handles` imports `test_session`. The browser module then imports it from `test_session`. This lands in Task 3, the first task that uses it.
3. **MED: the swatch click and the error select call `card.redraw_if_moved()`, never `redraw()`.** `redraw()` ignores the signature, so the "signature missing `self.error`" mutation survived. With `redraw_if_moved()` the "exactly one `update_figure`" assertion catches it.
4. **MED: add `test_a_criterion_that_draws_only_a_shape_has_a_swatch` to Task 4.** Stage `{"metric": "nrmse", "variable": "x", "window": {"start": 0.2, "end": 0.6}}`, which draws one window shape and no trace, and assert its row has a swatch. Task 1's stamp puts `meta.leaf` on the range's anchor traces, so the range alone cannot catch the "`drawable` from traces only" mutation.
5. **MED: the swatch's click is stopped in the browser, and only the browser can show it.** The user simulation's click does not bubble to parents. The handler is `.on("click", handler, js_handler="(e) => { e.stopPropagation(); emit(); }")`. Task 4's browser test asserts `.dstf-crit-open` and `.dstf-editor` counts are 0 after a swatch click. That is the test that catches "the swatch click reaching the row".
6. **MED: `TestPage.reveal(path)` records the path only if the node exists.** Apply is synchronous, so a refused Add leaves no node. The probe showed a refused Add's reveal opening a LATER valid add by itself. Rule: record only when `node_or_none(session.tree(model), path) is not None`. Add `test_a_refused_add_reveals_nothing` to Task 5.
7. **MED: a form's title is read inside the icon's click, never while the cluster is built.** `node_text` and `leaf_name` need `tree_view` nodes. Reading `tree_view` in `_cluster` at build time broke three existing cost tests (`tree_view` reads 5 against 0; `variable_texts` 42 against at most 2).
8. **MED: existing tests that move, per task, each updated in the task that moves it, saying why.**
   - Task 4, simulation: the six `_sans_rev(...) == shell.session.plot_figure(...)` sites in `tests/test_test_page.py` pass `visibility=card.visibility()`.
   - Task 4, browser: step 5b of `test_a_criterion_opens_below_its_row_and_the_plot_stages_and_refuses` needs range·x drawn, and it now starts hidden. Click its swatch, wait until its line is visible, then `window.scrollTo(0, 0)` (the click scrolls the plot off-screen).
   - Task 6, `tests/test_builtin_capabilities.py`: 14 tests move. The menu tests are rewritten for the row; `…built_by_the_menu_opening…` inverts and is deleted. The field labels become `Combinator` and `k — how many must pass`. The `unwrap:` icon is gone. The single-child unwrap test's refusal matches `the root has no parent`.
   - Task 6, `tests/test_test_page.py`: 4 tests move. The menu helper becomes an icon click. The apply buttons are found without `within=`, because the form row sits outside the host. `test_a_closed_menus_form…` becomes a Cancel test.
   - Task 6, browser: step 3 uses the `.dstf-form-row`'s Wrap and Cancel buttons instead of "apply wrap" and Escape.
   - The VARIABLE footer's button keeps the text `apply add`, because browser step 5d finds it by that name.
9. **LOW, facts for the builder:**
   - A criteria row is scoped by the marker `node:{index}:{path}` or `criterion:{index}:{path}`; `crit:{index}:{path}` is only the RowTarget's key and the `form-row:` marker's suffix.
   - `RowTarget`'s slot is set AFTER construction, because the host is built inside the row before the slot exists: construct the target, build the row and its host, create the slot, set `target.slot`, then `host.build()`.
   - The form row sits inside the slot column, which is the row's sibling; Task 5's test asserts the SLOT's position.
   - `spec_document.py` has no pointer helpers to sit beside. `node_at` and `node_or_none` go into a new UI-free `authoring/tree_pointer.py`, and `edit_on_plot.py`'s import of `_node_at` from `edit_tree` moves with them (mypy catches it).
   - `show()` repaints the swatches, because `drawable` is first known there.
   - The ActionBar row test goes in `tests/test_test_page.py`: `tests/test_capabilities.py` has no NiceGUI or shell.
   - `HostPanel.row` / `reveal` are added for the builtin tests that call them. The plan's claim that conformance needs them was vacuous: nothing type-checks the double.
   - Cosmetic, seen in the review's screenshots, not fixed here: after a click the icon's tooltip stays over the row above, and a swatch's tooltip can cover the footer button. Log them in the gate's hand-off.

10. **Owner ruling, 2026-09-24: the Plotly toolbar stands VERTICAL, on both tiers.** The Task 1–3 review found that the legend above the plot sits where the hover toolbar appears: at 900 px the report's 8-entry legend wraps to 2 rows and the toolbar covers "Tube upper points" and "Tube lower points". The options were rendered side by side (as built, vertical toolbar, no toolbar, legend below the plot, which collided with the x-axis title). The owner chose the vertical toolbar; the legend stays above the plot as the board drew it. Build: `_layout` sets `"modebar": {"orientation": "v"}`; a test in `tests/test_figures.py` pins it for `variable_figure` and `plot_figure`, and the named mutation is the key removed. LOOK, in the implementing task: a plot with an error series shown, because the error axes' tick labels sit on the same right edge as the vertical toolbar. If they overlap, stop and report; do not fix it by moving the legend. Lands in the correction batch after Task 4's review.

11. **From Task 4's review (2026-09-24; ACCEPT-WITH-FIXES), one correction batch with item 10:**
   - **MED: "is this criterion open" has ONE source, `path in page.editing_for(index)`.** `visibility()`, `paint_swatch()` and `_toggle()` read `card.open_path`, which is set after `show()`, while the rings read `page.editing_for`, which is set before it. Measured: the first open of a default-hidden criterion on a card not yet drawn sent the figure twice. The test asserts the first open of range·x on an undrawn card sends ONE figure. Mutation: `visibility()` reading `open_path` again.
   - **The error axes' automargin follows their visibility.** Spiked 2026-09-24 in Chromium: with `automargin: True` on a hidden axis, choosing Abs error and then No error left the right margin at 55 px (103 after NRMSE) instead of 20, through every later react. Setting each error axis's `automargin` equal to its `visible` in the figure restored 20 px every time. The figure layer does it where it sets the axis visible (with a Visibility); a figure without one keeps today's axes. Test in `tests/test_figures.py`; mutation: automargin left True on a hidden axis.
   - **`paint_swatch` updates classes only when they change** (`classes(replace=...)` or a compare first): a no-op sync re-sent every swatch (137 element updates against 101 before Task 4). Test: a second `paint` with nothing changed queues no update for a swatch.
   - **The error select is built wherever a figure is sent** (`show()` and `redraw()`), so a redraw that gains error traces gains the select.
   - **The swatch is reachable by keyboard:** `role="button"`, `tabindex="0"`, and Enter or Space toggles it the way a click does, with the same lock for the open criterion.
   - **`_drawable`'s docstring:** drop the longest-first sort, or say what it is for. The `+ "/"` already keeps `/1` from claiming `/10`.
   - **Browser:** `FIND_RING`'s comment no longer says the legend sits inside the plot, and a new assertion requires EVERY ring inside the zoomed range to hit the shape layer.
   - **Not changed, watch only:** a rebuild after a criterion is added or removed resets the swatch and error choices. Paths shift then, so keeping the choices would need a mapping no one asked for. Tooltips staying up after a click are item 9's cosmetic entry.

12. **Owner ruling, 2026-09-24: while an error axis shows, the plot reserves right margin for the vertical toolbar, on both tiers.** Item 10's LOOK found the vertical toolbar over the error axis's title and touching its tick labels, on the tool and on the report. Rendered side by side (as built; margin reserved; tick labels inside the plot, which still overlapped; the horizontal toolbar again), the owner chose to reserve the margin: the toolbar stays vertical and the legend stays above. Spiked: `margin.r` 95 while `yaxis3` shows cleared the toolbar at 1020 px (plot area 835 px against 875), and the margin returns when no error axis shows. Build:
   - **Tool:** the figure built with a Visibility whose `error` is set carries `margin.r` equal to a named constant (the reserve); otherwise the layout's own `r`.
   - **Report:** `templates/axes.js`, which already shows a secondary axis while a trace on it is visible, also sets `margin.r` to the reserve while ANY secondary axis shows and back to the layout's own value when none does. The report's legend can show signed and NRMSE together, so `yaxis3` and `yaxis4` (autoshifted outward) can show at once.
   - **Measure first:** in Chromium, with the vertical toolbar showing, every combination the tier allows (the tool: each single series; the report: each single series and signed plus NRMSE together). Assert in browser tests that no `g.y3tick`/`g.y4tick` text box or axis title box intersects `.modebar`. Pick the reserve from those measurements; if no single constant clears every combination, STOP and report the measurements.
   - **Mutations:** the reserve not set in the tool's figure; axes.js not restoring the margin when the last secondary axis hides.

13. **Owner rulings, 2026-09-24, after item 12's review (ACCEPT-WITH-FIXES). They SUPERSEDE item 12: the margin reserve is deleted.**
   - **Why item 12 failed.** A fixed reserve cannot know how wide the tick labels will be. Errors around 1e-5 are normal for a passing test, and they gave labels like `0.00002000` that reached the toolbar at every reserve measured (the report's `margin.r` grew to 147 under the toolbar). The toolbar and the right-hand axes were competing for the same strip inside the figure.
   - **Ruling 1: the toolbar gets its own gutter, outside the figure, on both tiers.** Spiked in Chromium over the Session's figure: the plot element is 34 px narrower than its container, and `.modebar-container` sits at `right: -34px`. Result: ZERO label or title boxes intersect `.modebar` for abs alone, signed with NRMSE, and both of those with 1e-5 labels (before: 3 each). The plot is 34 px narrower at all times. Build it with CSS: the tool's in `shell/style.py`, scoped to the Test page's plots; the report's in the template's stylesheet wherever its plots render (find by content). A named constant or CSS variable sets the width once per tier. DELETE `ERROR_MARGIN_R` and every use (the figure layer, `templates/axes.js`, their tests), and restore axes.js to showing and hiding axes only. KEEP item 11's rule that automargin follows visibility. The browser tests that assert no label or title intersects `.modebar` stay on both tiers, and gain the 1e-5 case (scale the fixture's error series by 1e-4).
   - **Ruling 2: every y axis uses Plotly's automatic tick labels with `e` exponents, on both tiers.** Drop `tickformat: ".4g"` from `yaxis`, `yaxis3` and `yaxis4`, and set `exponentformat: "e"`. Measured: 0.25 reads `0, 0.1, 0.2` (was `0.000, 0.1000, 0.2000`); 2.5e-5 reads `0, 1e−5, 2e−5` (was `0.000, 0.00001000, 0.00002000`, 54 px, now 23 px); 250 reads `0, 100, 200`. Hover keeps 4 significant digits: set `hoverformat: ".4g"` on each of those axes, and leave every trace's own `hovertemplate` alone. A real-mouse hover test reads `.hoverlayer text` and asserts a 4-digit value still shows. Re-bless the figure goldens, and check the diff is only these keys.
   - **Review fixes batched here:**
     - (a) In the Visibility branch of `variable_figure`, `yaxis4` is anchored to `"x"` with `autoshift` removed. With only one error axis ever showing in the tool, NRMSE otherwise floats about 48 px off the plot after Abs has shown. The report keeps `free` + `autoshift`: its legend can show both axes.
     - (b) Pin `role="button"`: assert the attribute. Pin `preventDefault` on Space: assert `window.scrollY` is unchanged after Space on a swatch in a scrolled page.
     - (c) Remove the prose that describes the reserve and the NRMSE float as design (the reserve comment, the browser comment "NRMSE's axis sits further out").
     - (d) The axes.js test page gets `<meta charset="utf-8">`.
     - (e) The "axes.js is loaded by the tool" prose stays for Task 8.
   - **Mutations:** the gutter CSS removed on either tier (the intersect tests catch it); `hoverformat` dropped (the hover test); `exponentformat` dropped (a figure test); yaxis4 left `free` in the tool's figure (a figure test asserting the anchor).

## Conventions for every task

The "Conventions for every task" block of `docs/superpowers/plans/2026-09-20-the-test-page.md` holds verbatim (gates and how to read them; red before green, shown; named mutations run in a `git archive` copy with an ABSOLUTE exported `PYTHONPATH`, `PYTHONDONTWRITEBYTECODE=1`, the module's `__file__` printed and ASSERTED under the archive first; commit by path with `-F <own msg file>`, never `--amend`, never `git stash` — not even inside a one-liner; never run `dstf` against `testing_output/` or a real work dir; done-checks are predicates; locate code by content; no internal record id in a tracked file; `docs/ideas.md` and `.soul/` untouched), and so do its "Corrections" items 2, 3, 6 and 8 where they still apply (the user simulation's rules; mypy/ruff idioms; the review harness; the NiceGUI facts). `docs/decisions.md` gains exactly one entry, D133, in Task 8. Additionally:

- **Never touch `/mnt/d/Modelica/TRANSFORM-UnitTests` or `/mnt/d/Modelica/TRANSFORM-Library`.** Task 9 is the orchestrator's and works over a copy.
- **Every UI task LOOKS AND HOVERS.** The implementer takes a viewport screenshot of what the task changed, makes a thumbnail and READS it; and where text enters a Plotly template or the unified hover's set of traces changes, a real-mouse Playwright read of `.hoverlayer .hovertext` asserts it (a screenshot cannot show a hover).
- **A design change seen in a LOOK is written into the brief as a rule before it is built.** An implementer who sees something the plan did not foresee stops and reports it; it is not fixed in passing.
- Each task leaves the whole suite green (`uv run pytest -m "not playwright"`, `uv run pytest -m playwright` for Tasks 4, 6, 7), and `ruff check`, `ruff format --check`, bare `mypy` clean.

---

### Task 1 — Figures: `Visibility`, the legend above, `meta.leaf`

**Files:** `src/dstf/figures/plotly.py`; tests `tests/test_figures.py`, `tests/test_plot_figure.py`.

- [ ] **RED** — `tests/test_figures.py`, over the existing view fixtures that carry a tube leaf, a range leaf and error series (find them by content: a `VariableView` with `errors` and two leaves):
  - `test_every_leaf_trace_names_its_leaf`: every trace built from a leaf (bands, marker groups, the hline anchor traces) has `meta["leaf"] == leaf.path`; the actual/reference/overlay/error traces have no `leaf`.
  - `test_the_legend_sits_above_the_plot_area`: `fig["layout"]["legend"] == {"orientation": "h", "x": 0, "xanchor": "left", "y": 1, "yanchor": "bottom", "bgcolor": "rgba(0,0,0,0)"}` for `variable_figure` and for `plot_figure` of two views.
  - `test_without_visibility_the_figure_is_the_reports`: no `visibility` → every leaf trace keeps its `showlegend`, errors stay `legendonly` with their legend entries, no trace or shape has `visible: False` (the report's figure is unchanged but for the legend).
  - `test_visibility_hides_a_leafs_traces_and_shapes`: `Visibility(hidden=frozenset({RANGE}))` → every trace with `meta.leaf == RANGE` has `visible is False`, every shape whose `name` starts with `RANGE + "/"` has `visible is False`, the tube's traces and rings have no `visible` key; the SHAPE LIST has the same length and order as without it (spike fact 2: indices must not move).
  - `test_visibility_puts_only_signals_in_the_legend`: with `Visibility()` every leaf trace and every error trace has `showlegend is False`; the actual / reference / overlay traces are untouched.
  - `test_visibility_shows_the_chosen_error_and_its_axis`: `error="error/abs"` → that trace `visible is True`, the other two `visible is False`, `layout.yaxis3.visible is True`, `layout.yaxis4.visible is False`; `error="error/nrmse"` → `yaxis4` visible, `yaxis3` not; `error=None` → all three `False`, both axes `False`.
  - `test_visibility_refuses_a_bare_string_and_an_unknown_error`: `Visibility(hidden="/metrics/children/0")` → `TypeError` (a str's membership is by substring); `Visibility(error="error/bogus")` → `ValueError` naming the three ids.
  - `tests/test_plot_figure.py::test_a_plot_of_several_takes_visibility`: `plot_figure([vx, vy], visibility=Visibility(hidden=frozenset({<a leaf of vy>})))` hides that leaf's traces and shapes and keeps the families' legend entries (`showlegend` of the first actual per family unchanged).
  - Run: `uv run pytest tests/test_figures.py tests/test_plot_figure.py -q` → the new tests FAIL (ImportError on `Visibility`, then the legend dict) on unchanged code. Show it.
- [ ] **GREEN** — in `figures/plotly.py`:

```python
#: Where the legend sits, on both tiers: above the plot area, one line.
#: Plotly grows the top margin to fit it (measured: 25 -> 41 px), so it never
#: sits on the trace start or on a ring (the 2026-09-21 board, option b).
LEGEND = {
    "orientation": "h",
    "x": 0,
    "xanchor": "left",
    "y": 1,
    "yanchor": "bottom",
    "bgcolor": "rgba(0,0,0,0)",
}


@dataclass(frozen=True)
class Visibility:
    """What the tool's page chose to show on one plot (legend b).

    ``hidden``: the leaf paths whose drawing is off. ``error``: the one error
    series shown — a key of :data:`ERROR_AXES` — or ``None``. A figure built
    WITH a Visibility puts only signals in its legend: the criteria are
    toggled from their rows and the errors from one select. A figure built
    without one (the report) keeps every legend entry."""

    hidden: frozenset[str] = frozenset()
    error: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.hidden, str):
            raise TypeError(
                "hidden takes a collection of leaf paths, not a str "
                "(a str matches by substring)"
            )
        if self.error is not None and self.error not in ERROR_AXES:
            raise ValueError(
                f"error {self.error!r} is none of {', '.join(ERROR_AXES)}"
            )
```

  - `_layout(...)`: `"legend": dict(LEGEND)`.
  - `_leaf_traces(view, ctx, *, editable, rings_for=None, visibility=None)`: after each builder returns and after `_marker_traces`, for every trace of that leaf: `trace["meta"] = {**trace.get("meta", {}), "leaf": leaf.path}`; when `visibility is not None`: `trace["showlegend"] = False`, and when `leaf.path in visibility.hidden`: `trace["visible"] = False` and every shape and ring of that leaf gets `"visible": False`. Rings keep being built for the open leaf only (`rings_for`); nothing else about them changes.
  - `_error_trace(s, ctx, visibility)`: with a visibility, `showlegend: False` and `visible: s.id == visibility.error`; without, as today.
  - `variable_figure(..., visibility=None)` threads it; after `_layout`, when `visibility is not None and visibility.error is not None and view.errors`: `layout[ERROR_AXES[visibility.error].replace("y", "yaxis")]["visible"] = True` (`"y3"` → `"yaxis3"`).
  - `plot_figure(..., visibility=None)` threads it to both branches (a plot of several has no error traces, so `error` has nothing to act on there — say so in its docstring).
  - Run the two files → PASS; run the whole non-playwright suite → green.
- [ ] **Mutations the tests must catch:** the `leaf` stamp skipped for marker traces; hidden shapes REMOVED from the list instead of `visible: False`; the chosen error's axis not set visible; `showlegend` forced off for leaf traces also when `visibility is None`; `__post_init__`'s str check deleted.
- [ ] **Commit:** `figures: a Visibility the page chose — hidden leaves, one error series and its axis; the legend above the plot area`.

### Task 2 — The metric's default: `MetricDescriptor.drawn_by_default`

**Files:** `src/dstf/comparison/registry.py`, `src/dstf/comparison/builtins.py`; test `tests/test_metric_registry.py`.

- [ ] **RED** — `test_a_metric_says_whether_its_drawing_shows_by_default`: `{d.name: d.drawn_by_default for d in metrics()} == {"nrmse": True, "tube": True, "points": True, "range": False, "event-timing": True, "dominant-frequency": True, "scalar": False}`; and a descriptor registered without the field defaults to `True`. Fails on unchanged code (AttributeError). Show it.
- [ ] **GREEN** — `MetricDescriptor` gains, after `edit_handle`:

```python
    #: Whether a closed criterion of this metric draws on the Test page until
    #: its swatch says otherwise (legend b). Off for metrics whose drawing is a
    #: bound across the whole plot — range, scalar — which would otherwise
    #: cover every plot they sit on. The open criterion always draws.
    drawn_by_default: bool = True
```

  and the `range` and `scalar` registrations in `builtins.py` pass `drawn_by_default=False`. Its docstring paragraph names the field.
- [ ] **Mutation:** `scalar` left at the default.
- [ ] **Commit:** `registry: a metric says whether its drawing shows by default`.

### Task 3 — Session: the figure reads take a `Visibility`

**Files:** `src/dstf/authoring/session.py` (`Session.figure`, `Session.plot_figure`, `SessionView.figure`, `SessionView.plot_figure`); test `tests/test_session.py`.

- [ ] **RED** — `test_plot_figure_passes_the_pages_visibility`: over `_two_var_project` with `tests/test_handles.py::TREE` staged on A (the fixture `test_author_test_page_playwright._page_project` builds; reuse its spec edit by content), `session.plot_figure(A, 0, editing=(), visibility=Visibility(hidden=frozenset({RANGE_X})))` has every `meta.leaf == RANGE_X` trace `visible is False`, and `session.figure(A, "x", visibility=Visibility(error="error/signed"))` shows `error/signed` and `yaxis3`. `SessionView` exposes the same keyword (its public-name pin, `TestSessionView::test_exposes_readers_and_no_writer`, is unchanged — no new name). Fails with `TypeError: unexpected keyword`. Show it.
- [ ] **GREEN** — add `visibility: Visibility | None = None` to the four signatures and pass it through to `variable_figure` / `plot_figure`. The cached base (`_editable_view`) does not depend on it; no cache key changes.
- [ ] **Mutation:** `plot_figure`'s one-series branch dropping `visibility` when it calls `self.figure`.
- [ ] **Commit:** `session: the figure reads take the page's Visibility`.

### Task 4 — The plot card: a swatch per criterion, one error select

**Files:** `src/dstf/authoring/shell/plot_card.py`, `src/dstf/authoring/shell/style.py` (the swatch's CSS); tests `tests/test_test_page.py` (simulation), `tests/test_author_test_page_playwright.py` (browser).

The rule, a module function in `plot_card.py`:

```python
def drawn(leaf: dict, choice: bool | None, is_open: bool) -> bool:
    """Whether one criterion draws on its plot (legend b). The open one
    always does — the plot is its editing surface. Else the swatch's choice,
    when there is one; else a failing criterion does, so a FAIL never starts
    hidden; else the metric's own default."""
    if is_open:
        return True
    if choice is not None:
        return choice
    if leaf.get("passed") is False:
        return True
    desc = registry.metric(str(leaf.get("metric")))
    return desc is None or desc.drawn_by_default
```

- [ ] **RED (simulation)**, `tests/test_test_page.py`, over the `_page_project` spec (A: `and[tube · x, range · x, range · y]`; tube and range·y FAIL, range·x passes) — plot 0 draws x:
  - `test_a_closed_range_starts_hidden_and_a_failing_one_drawn`: after `show()`, the figure plot 0 last sent (`card.plot.figure`) has range·x's traces and lines `visible is False` and the tube's visible; plot 1 (y) has range·y DRAWN (it fails).
  - `test_a_swatch_toggles_its_criterions_drawing`: click `swatch:0:/metrics/children/1` → the sent figure now draws range·x; click again → hidden; exactly one `update_figure` per click (count sends through a wrapper on `card.plot.update_figure`), and the other cards were not re-sent.
  - `test_a_failing_criterions_swatch_hides_it` (the owner's 2026-09-24 ruling): on plot 1, range·y FAILS and starts drawn; its swatch is ENABLED; a click sends a figure with range·y's lines `visible is False`; its row's pill still reads FAIL, and so does the card's pill.
  - `test_the_open_criterions_swatch_cannot_hide_it`: open range·x's editor → drawn; its swatch is disabled and its tooltip reads `the open criterion always draws`; close the editor → range·x is hidden again (no choice was made).
  - `test_the_error_select_shows_one_series`: plot 0's `error-select:0` offers `No error`, `Signed error`, `Abs error`, `NRMSE (per-point)` (the labels of `figures.plotly._LABELS`); choosing `Abs error` sends a figure with `error/abs` visible and `yaxis3.visible is True`; a plot of several series (plot 2) has no error select.
  - `test_a_swatch_is_there_once_the_plot_is`: before `show()` no `swatch:` marker exists on the card; after it, one per criteria row whose leaf has a trace or shape with its path (`meta.leaf`, shape `name` prefix).
  - Show all six failing on unchanged code.
- [ ] **GREEN:**
  - `PlotCard.__init__`: `self.choices: dict[str, bool] = {}`, `self.error: str | None = None`, `self.drawable: frozenset[str] = frozenset()`; the error select slot in the toolbar row just before Focus (`self.error_slot = ui.row()`), empty until the plot is drawn.
  - `PlotCard.visibility(view) -> Visibility`: `hidden = frozenset(path for leaf in self._leaves_all(view) if not drawn(leaf, self.choices.get(path), path == self.open_path))`, `error=self.error`. `_leaves_all` is every leaf of the view whose `variable` is in `self.resolved.series`.
  - `_figure()` passes `visibility=self.visibility(self._view())`; after building it, `self.drawable` = the paths found in `meta.leaf` of its traces and in the shape names (the part of `name` up to the leaf path — match against the view's leaf paths, longest first).
  - `signature()` adds `sorted(hidden)` and `self.error`.
  - `CriterionRow` gains a swatch left of its pill: `ui.element("span").classes("dstf-swatch")`, marked `swatch:{index}:{path}`, with a tooltip `Hide this criterion's drawing` / `Show this criterion's drawing` / `the open criterion always draws`; filled with the status colour the pill uses when drawn, an outline of it when hidden; `set_visibility(path in card.drawable)`. Its click (stopped from reaching the row, like `InlineHost`'s column) sets `card.choices[path] = not currently_drawn` and calls `card.redraw()`. `CriterionRow.paint` repaints it.
  - After `show()` draws the plot: if any trace has `meta.kind == "error"`, build `ui.select({"none": "No error", **{id: _LABELS[id] for id in the figure's error ids}}, value="none")` in `error_slot`, marked `error-select:{index}`, `props("dense options-dense")`; its change sets `self.error = None if value == "none" else value` and calls `self.redraw()`.
  - `PlotCard.paint` also repaints the swatches (a verdict that flips changes a default).
  - The toolbar comment "the legend already toggles the overlays and the error traces" is rewritten: the legend toggles the signals and overlays; the criteria are toggled from their rows; the errors from the select.
- [ ] **RED then GREEN (browser)** — `tests/test_author_test_page_playwright.py::test_a_swatch_hides_a_criterion_and_the_zoom_stays`: zoom plot 0 (`ZOOM_AND_STAMP`), click range·x's swatch → `READ_PLOT`'s shapes show the two range lines and the zoom is unchanged and the stamp survives (a `react`, not a `newPlot`); open the tube → its rings are grabbable with the legend above (FIND_RING's hit test lands on the shape layer, not the legend); a REAL-MOUSE hover over the plot at the tube's x lists `Tube upper points` in `.hoverlayer .hovertext` while the tube draws and does NOT after its swatch hides it (Mind: a screenshot cannot show a hover). Error select → `Abs error`: `_fullLayout.yaxis3.visible` is true. Screenshot → thumbnail → READ: the legend is one line above the plot and covers nothing.
- [ ] **Mutations:** `drawn()` returning `True` for `passed is False` before it reads the choice (reverses the ruling); `drawn()` ignoring `is_open`; the signature missing `self.error` (the select sends nothing); the swatch click reaching the row (it opens the editor too); `drawable` computed from traces only (a range leaf draws only shapes + invisible anchor traces — its swatch must still show).
- [ ] **Commit:** `test page: each criterion's drawing is toggled from its row; one error select per plot`.

### Task 5 — `Panel.row` and `Panel.reveal`: one thing open at a time

**Files:** `src/dstf/authoring/capabilities.py` (the `Panel` protocol), `src/dstf/authoring/shell/actions.py` (`ShellPanel`, `ActionBar`, `InlineHost`), `src/dstf/authoring/shell/test_page.py`, `src/dstf/authoring/shell/tree_card.py`, `src/dstf/authoring/shell/plot_card.py`, `src/dstf/authoring/shell/style.py`; tests `tests/capability_host.py` (the double), `tests/test_capabilities.py`, `tests/test_test_page.py`.

The protocol, added to `Panel`:

```python
    def row(self, title: str, build: Callable[[Callable[[], None]], None]) -> None:
        """Open a form row below the row this panel is drawn in, titled
        ``title``. ``build(close)`` draws its content — fields, a sentence,
        its buttons; ``close`` takes the row away. ONE thing is open at a
        time on a page: this closes any other row and the criterion editor.
        While it is open, ``build`` is called AGAIN, into an emptied row, when
        an edit staged elsewhere moves the node the panel is for — a form
        never shows a value the node no longer has. Where the panel has no
        row (a dialog), the form is drawn in the panel's own slot."""
        ...

    def reveal(self, leaf_path: str) -> None:
        """Show that criterion's editor once the page has drawn it — after
        an add, the new criterion. A no-op where there is no page."""
        ...
```

- [ ] **RED** —
  - `tests/test_capabilities.py::test_a_panel_row_opens_in_a_dialogs_slot`: an `ActionBar` over a test capability whose panel calls `ui_panel.row("T", build)` shows `T` and `build`'s content inside the dialog; `close` removes them; `reveal` does nothing and raises nothing.
  - `tests/test_test_page.py`, with a probe capability registered at NODE and at CRITERION for the test (the existing probe-registration pattern in that file; find it by content), whose panel draws one button `probe-open:{path}` calling `ui_panel.row(f"probe {path}", build)` and whose `build(close)` draws a label `probe-body:{path}` with a counter of how many times it was built and a `probe-close` button:
    - `test_a_row_opens_under_the_tree_row_it_was_asked_from`: open the tree, click `probe-open:/metrics/children/0` within `tree-row:/metrics/children/0` → `form-row:tree:/metrics/children/0` exists, is a sibling AFTER that tree row and BEFORE the next node's row, carries the title; the tree row has class `dstf-row-open`.
    - `test_one_thing_is_open_at_a_time`: open a row under tree node 0, then under criteria row `/metrics/children/1` of plot 0 → the first is gone; open the tube's editor → the row is gone; open a row → the editor is closed (`page.open is None`) and the plot's rings went with it (the figure re-sent has no circle shape).
    - `test_a_click_on_the_criteria_row_closes_its_form_and_opens_nothing`: row open under criteria row R; click R → the form closes and `page.open is None`.
    - `test_an_edit_staged_elsewhere_rebuilds_the_open_row`: row open under the root; stage a `replace` of the root's `children/0/tube/...` param through `session.apply` (same shape) → `build` ran a second time (counter 2) and the row is still open; stage a shape change (add a child) → the row is closed (the rows it hung under were rebuilt).
    - `test_reveal_opens_the_criterion_after_the_page_redraws`: a probe act that stages an add of `{"metric": "tube", "variable": "x"}` under the root and calls `ui_panel.reveal("/metrics/children/3")` → after the next sync, `page.open == (0, "/metrics/children/3")` and its editor exists.
  - Show them failing.
- [ ] **GREEN:**
  - `capabilities.Panel`: the two methods above (import `Callable`).
  - `ShellPanel.__init__(submit, figure, row=None, reveal=None)`; `row(title, build)` delegates to `row` when given, else raises `RuntimeError("no row at this placement")`; `reveal(path)` calls `reveal` when given, else does nothing.
  - `ActionBar.open`: the panel's `row` draws into a column appended to the dialog's slot: a title label and `build(close)`, `close` clearing that column. `reveal` is a no-op.
  - A small class in `actions.py`:

```python
class RowTarget:
    """Where an InlineHost's panels open their rows: a slot the page owns
    under one row, and the page's one-open-at-a-time rule. ``key`` names the
    slot page-wide (``tree:<path>`` or ``crit:<index>:<path>``); ``node`` is
    the node path the forms act on — what the page watches to rebuild an
    open row."""

    def __init__(self, page: Any, key: str, slot: ui.column, row: ui.row, node: str) -> None:
        self.page, self.key, self.slot, self.row, self.node = page, key, slot, row, node

    def open(self, title: str, build: Callable[[Callable[[], None]], None]) -> None:
        self.page.open_row(self, title, build)
```

  - `InlineHost(..., row: RowTarget | None = None)`: its `ShellPanel` gets `row=row.open if row else None` and `reveal=shell-page reveal` (the page's `reveal`, reached through `row.page` when there is one; a host with no row passes none).
  - `TestPage`: `self.form: tuple[RowTarget, str, Callable, str] | None` (target, title, build, the node's raw JSON when built). `open_row(target, title, build)`: `_close()` the editor and `_close_row()` any row, redraw moved cards once; then in `target.slot`, a `ui.column().classes("dstf-form-row w-full gap-2")` marked `form-row:{target.key}` holding `ui.label(title).classes("text-subtitle2")` marked `form-title:{target.key}` and `build(close)`; `target.row.classes(add="dstf-row-open")`; record the node's raw JSON (`json.dumps(_node_or_none(session.tree(model), target.node), sort_keys=True)` — import the helper from `builtin/edit_tree.py`? NO: shell imports no builtin; put `node_at(tree, path) -> dict | None` in `authoring/spec_document.py` beside the pointer helpers and have `edit_tree.py` use it too). `_close_row()`: clear the slot, remove the class, `self.form = None`. `open_criterion` calls `_close_row()` first. `sync`: after the tree card and cards are refreshed, if `self.form` is set and its target's slot no longer exists (its rows were rebuilt — track by `target.slot.is_deleted` or by the card/tree having rebuilt), `self.form = None`; else if the node's raw JSON moved, clear the row's column content and call `build(close)` again, re-recording the JSON. `reveal(path)`: `self.pending_reveal = path`; at the end of `sync`, when set, `open_criterion(path)` then clear it.
  - `TreeCard._row`: after the row, `slot = ui.column().classes("w-full gap-0")`; the row's `InlineHost` gets `row=RowTarget(page, f"tree:{path}", slot, row, path)`.
  - `CriterionRow`: its host gets `row=RowTarget(page, f"crit:{index}:{path}", self.slot, self.row, path)`; the row's click handler becomes `page.row_click(index, path)`: when `page.form` targets this row, close the row and open nothing; else `toggle_criterion`.
  - `style.py`: `.dstf-form-row` shares `.dstf-editor`'s indent and left rule (find `.dstf-editor` by content and add the selector to it); `.dstf-row-open` shares `.dstf-crit-open`'s lit background.
  - `tests/capability_host.py::HostPanel` implements `row` (draws inline in its own column) and `reveal` (records the path in a list the test reads), so the conformance tests hold for every capability.
- [ ] **Mutations:** `open_row` not closing the editor; `sync` rebuilding the row on every sync (the counter test catches it: an unrelated event must not rebuild); `row_click` falling through to `toggle_criterion` after closing the row; `reveal` opening before the rebuild (the path does not exist yet — the test sees `open is None`).
- [ ] **Commit:** `test page: a panel opens a row under its host's row; one thing open at a time`.

### Task 6 — Edit tree: the forms as rows (board 1A–1D)

**Files:** `src/dstf/authoring/builtin/edit_tree.py`; tests `tests/test_builtin_capabilities.py`, `tests/test_test_page.py`, `tests/capability_host.py` (delete `open_menu`); browser `tests/test_author_test_page_playwright.py`.

What each form is (titles and sentences are the board's, to the character; `{node}` is the tree row's own text — `node_text` for a combinator, `leaf_name` for a leaf — so move `leaves_of`, `leaf_name` and `node_text` — the last needs the first — from `shell/tree_card.py` into a UI-free module both may import, `authoring/tree_text.py`, and have `tree_card.py` and `plot_card.py` import them from there):

| Icon | Title | Fields | Sentence under the fields | Buttons |
|---|---|---|---|---|
| `+` on a combinator | `Add a criterion under {combinator}` | Variable, Metric | `Added with the metric's defaults as the last child; its editor opens under its plot.` | Cancel, Add |
| `+` on a leaf | `Add a criterion beside {node}` | Variable, Metric | same | Cancel, Add |
| wrap | `Wrap {node} in a combinator` | Combinator, then that kind's fields | per kind, below | Cancel, Wrap |
| edit (combinator only) | `Edit this combinator` | Combinator, then that kind's fields | per kind, below | Unwrap, Cancel, Apply |

Per-kind fields (only the chosen kind's; changing the select rebuilds the fields column, keeping values typed for a kind the user returns to): `k-of-n` → `k — how many must pass` (number); `weighted` → `Weights, one per child` (text), `Threshold` (number), `Direction` (select: `lower is better` ↔ `less`, `higher is better` ↔ `greater`; the node's own, default `less`). `and` / `or` / `warn` → none.

Wrap sentences: `and` → `The criterion becomes the only child of a new and; add siblings to it with its +.`; `or` → `… a new or; add siblings to it with its +.`; `k-of-n` → `… a new k-of-n; add siblings to it with its +.`; `warn` → `A failure of this criterion becomes a warning; the test still passes.`; `weighted` → `The criterion becomes the only child of a new weighted node, with the one weight above.` (For a combinator being wrapped, "criterion" reads "node".) Edit's sentence: `Changes this node's kind and fields; its children stay.`

Unwrap inside Edit: one child → the node is replaced by it (today's act). Several children → they take the node's place in its parent, in order: ops `remove {path}` then `add {parent}/children/{i+j}` for each child `j` (the act validates through the Session like every other; a `warn` or `weighted` parent refuses by name, shown in the host's error label). On the ROOT with several children Unwrap is disabled with the tooltip `the root has no parent to take its children`.

`+` greyed: on a `warn` node or a leaf whose parent is a `warn` → tooltip `a warn takes exactly one child`; on a `weighted` node or a leaf whose parent is a `weighted` → `a weighted node has one weight per child; a child cannot be added alone`.

- [ ] **RED** — `tests/test_builtin_capabilities.py` (the panel through `HostPanel`, the act directly):
  - `test_wrap_opens_a_row_titled_for_the_node_with_only_its_kinds_fields`: clicking `wrap:{leaf}` calls `row` with `Wrap nrmse · x in a combinator`; the row shows `Combinator` = `and` and no `k`/`Weights`/`Threshold`/`Direction`; switching to `k-of-n` shows `k — how many must pass` only; to `weighted` shows the three; the sentence under them is the kind's.
  - `test_edit_carries_unwrap_and_the_nodes_direction`: Edit on a `weighted` node with `direction: "greater"` shows `higher is better`; Apply with `lower is better` stages `replace …/direction "less"`; `Unwrap` is in the row.
  - `test_unwrap_of_several_children_moves_them_into_the_parent`: `and[k-of-n[a, b], c]` unwrap `/metrics/children/0` → ops `remove /metrics/children/0`, `add /metrics/children/0 a`, `add /metrics/children/1 b`; applied, the tree is `and[a, b, c]`.
  - `test_unwrap_is_disabled_on_a_root_with_several_children` (tooltip text as above).
  - `test_plus_is_grey_under_warn_and_weighted` (both tooltips).
  - `test_add_reveals_the_new_criterion`: Add under `and[l0, l1, l2]` → the host double's reveal list is `["/metrics/children/3"]`; Add beside a ROOT leaf (which wraps it in `and`) → `["/metrics/children/1"]`.
  - `test_cancel_stages_nothing`: open wrap, Cancel → no pending op, `close` called.
  - `tests/test_test_page.py`: `test_a_wrap_form_opens_under_the_criteria_row_and_applies` (board 1D over `_page_project`: `wrap:/metrics/children/0` within `crit:0:/metrics/children/0` → `form-row:crit:0:/metrics/children/0`, choose `warn`, Wrap → the pending op wraps it; the row is gone); `test_a_discarded_edit_reopens_on_the_nodes_value` (the 3b stale-form probe, rewritten for the row: Edit a `k-of-n` root to k=2, Apply, Discard, open Edit → `k` reads the node's value, and an Apply stages nothing stale).
  - The existing tests that open menus (`open_menu(...)` call sites; find by content) now click the icon; `open_menu` is DELETED from `tests/capability_host.py` along with its docstring.
  - Show the new tests failing.
- [ ] **GREEN** — in `edit_tree.py`: `_lazy_menu` is deleted, with its docstring (the facts it records move to `_cluster`'s docstring: built on the icon's click, from the tree as it is then; rebuilt by the page when the node moves); `_combinator_controls(ui, node)` becomes `_combinator_fields(ui, node) -> Callable[[], dict]` building the select and a fields column rebuilt on the select's change; `_kind_fields` reads `direction`; `act("unwrap")` handles several children; `_cluster`'s icons call `ui_panel.row(title, build)` with the builders above, each `build(close)` drawing fields + sentence (`.classes("dstf-why")`) + buttons (`Cancel` → `close`, the action → `close()` then `ui_panel.submit(...)`; after an Add's submit, `ui_panel.reveal(new_path)` where `new_path` is computed from the node as it was when the row was built). Marks kept: `add:`, `wrap:`, `edit:`, `remove:`, `apply-add:`, `apply-wrap:`, `apply-edit:`; new: `unwrap-in-edit:{path}`, `cancel:{path}`. The separate `unwrap:{path}` icon is deleted (Unwrap lives in Edit).
- [ ] **Browser** — `test_a_form_row_opens_under_its_node_and_nothing_covers_it`: open the tree, click the wrap icon on the tube's tree row → the form row is BELOW that row (its box's top ≥ the row's bottom) and the next node's row moved down; click the tube's criteria row editor open → the form row is gone. LOOK: thumbnail READ; the title names the node; nothing overlaps.
- [ ] **Mutations:** the fields column not rebuilt on a kind change; unwrap's adds in reverse order; `reveal` given the path computed AFTER the add (off by one); `+` enabled under `warn`.
- [ ] **Commit:** `edit tree: the forms open as a row under the node — one kind's fields, a sentence, Cancel; Unwrap inside Edit`.

### Task 7 — Browser: the page as one piece

**Files:** `tests/test_author_test_page_playwright.py`.

- [ ] `test_the_legend_holds_signals_and_sits_above`: plot 0 of `_page_project`: the legend's items are `Actual`, `Reference` (and overlays if any) — no `Tube`, `Range`, error names; the legend's bounding box bottom ≤ the plot area's top (`_fullLayout._size.t` + the plot's top). Real-mouse hover over the actual trace: `.hoverlayer .hovertext` includes the actual's hover text (the label template still renders its label, not `%{meta.label}` — Mind 9's trap, re-checked on the changed figure).
- [ ] Every existing browser test still passes (the ring finder's comment about the legend covering a ring is rewritten: the legend no longer sits in the plot area).
- [ ] Viewport screenshots of: the page with the tree open and a wrap row open; plot 0 with the error select on `Abs error`; plot 0 with range·x's swatch on. Thumbnails READ by the implementer; kept under `DSTF_KEEP_SHOTS` for the gate.
- [ ] **Commit:** `test page: the legend, the swatches and the form row in Chromium`.

### Task 8 — Docs, D133, the roadmap, the axes prose

- [ ] `CLAUDE.md` "Running the Tool" (the `dstf author` comment): the tree's `+` / wrap / edit open as a row under the node; a swatch on each criteria row shows or hides that criterion's drawing; one Error select per plot; the legend holds the signals. The Project Overview's sentence about the Test page, likewise, in its own words.
- [ ] `docs/usage.md` and `docs/architecture.md` where they describe the Test page or the figure layer's legend; `docs/extensibility.md` §"Metric" gains `drawn_by_default` and §"capability contract" gains `Panel.row` / `Panel.reveal`.
- [ ] `docs/decisions.md` D133: the form row (what moved, the one-open rule, Unwrap's several-children semantics, `+` greyed); legend b (signals only in the tool's legend, above the plot area on both tiers; the swatch; the error select; the metric default; the open criterion always draws, a failing one draws by default and its swatch can hide it, and why — the failure stays on its row, the card, the tree line, the header and the Suite page); the axes.js regression found and why the fix is structural.
- [ ] Roadmap `docs/superpowers/plans/2026-09-15-authoring-tool-redesign-roadmap.md` §3: a "variable identity BUILT" paragraph (D132: `unitTests.x[i]` is the name everywhere, the expression a label and soft check, the migration run on the owner's tree) and a "form row + legend BUILT" paragraph (D133).
- [ ] The axes prose: `templates/axes.js`' header no longer says the tool adds it; `report_suite.axes_js_source` is deleted if only tests call it (then `tests/test_axes_js_playwright.py` reads the file through `_TEMPLATE_DIR`), else its docstring says who; `figures/plotly.py::_layout`'s comment says the report's axes.js follows legend clicks and the tool's figure sets the axis itself.
- [ ] **Commit:** `docs: the form row and legend b; D133`.

### Task 9 — Gate over a COPY of TRANSFORM (orchestrator)

- [ ] Copy `TRANSFORM-UnitTests/ReferenceResults` and the last work dir to the scratchpad; sha256 every file of the owner's two trees before and after — identical.
- [ ] Counts of what the feature DID, over every test of the copy through `Session` (a script; no browser): per plot, legend entries in the tool's figure BEFORE (the commit preceding Task 1, a `git archive` copy, absolute `PYTHONPATH`, `dstf.__file__` asserted) and AFTER (HEAD) — expected after: only actual/reference/overlays; the number of plots whose legend shrank; zero plots whose trace count changed (`visible` changes, not removal); zero verdict flips (`compare` before/after, status per test).
- [ ] Every TRANSFORM leaf is nrmse, so the swatch is gated on a STAGED tree: in memory, on one TRANSFORM test with a reference, stage a tube and a range on its first variable; in Chromium: the range starts hidden, its swatch shows it, the zoom holds; the tube's rings drag with the legend above (a real-mouse drag stages an op); Error select → Abs error shows the error axis; a wrap row opens under a criteria row and Cancel stages nothing; Discard. Screenshots → thumbnails → READ, one of them the same zoom as `.soul/variable-identity-gate-2026-09-21/shot-b.png.thumb.jpg` showing the legend no longer over the trace start.
- [ ] The report: `compare --report` over the copy; one report page's screenshot READ — the legend above its plot, its criterion entries still there.
- [ ] Hand the owner: the counts and the pictures.
