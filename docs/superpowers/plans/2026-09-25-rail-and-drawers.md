# Rail and Drawers Implementation Plan (3c)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the Test page of `dstf author`, a 40 px labelled rail opens a Tests drawer and a Variables drawer, one at a time. The Variables drawer edits the test's plot layout on the plot you selected: add or remove a series, make a new plot. Each plot's toolbar gains Add series and Remove plot. The tree card's line gains Reset layout. The header's least-used items move into a ⋮ menu. Every layout edit is staged as ONE op on the entry's `plots` key.

**Owner rulings this plan builds on (2026-09-25, chat and the rendered board `rail-drawers-board`; recorded in the roadmap's "3c scoped" paragraph; not reopened):**
- **Split.** 3c is layout only. 3d adds the slice field and save-time expansion of a folded row into one criterion per index (ruling §3.10).
- **Selection (Q5 A).** The drawer's checkboxes act on the plot you CLICKED. A click on a plot card's toolbar or margin selects it. Add series selects its plot and opens the drawer. The first plot is selected until then. The drawer names its target ("Checkboxes act on plot 2 of 3").
- **The drawer does every layout edit (Q6 A).** Unchecking removes a series from the selected plot. "+ new plot" on a hovered row adds a plot below the selected one holding that variable, and leaves the variable where it was. The plot toolbar gains only Remove plot (and Add series). DESIGN.md's scheduled "series chips" are dropped.
- **A scored variable keeps a plot (Q7 A).** A variable a criterion scores cannot leave its last plot. Its checkbox greys and the row says why. The tool never creates that state; a hand-written spec that already has it keeps today's warning.
- **Reset layout (Q8 A).** It is a button in the tree card's line, shown only while the test's effective entry has a `plots` key. It stages removing the key.
- **Tests drawer (Q9).** It shows the Suite page's list: grouped by package, short names, status pills, a filter, the current test highlighted. It has no checkboxes. A click opens the test, and pending edits survive the switch.
- **Array folding (round 1 Q3 A).** A name folds on its last index whenever that index is a single integer, whatever comes before it: `massPorts[4, 1].C[]` is a family, `x[2,3]` is not.
- **Save names a new entry (round 1 Q4).** A pending edit that creates a model's first `test_spec.json` entry is said so on the Save button.
- **Header (Q10 A).** A ⋮ menu at the right end, at every width, holds Export, Open in another backend…, dashboard and Stop server, each with its full label. Save and Discard stay. This fixes the 900 px header that cut off "dashboard" and "Stop server".
- **Deferred to docs/ideas.md (Q11, Q12):** moving plots up and down; plot titles.
- **Not in 3c:** the small residuals of the last two builds (they wait for the owner's hands-on look), except the 900 px header.

**Planner's choices (not owner rulings; each is stated where it is built, and the owner may overrule any of them at the plan's review):**
1. "+ new plot" makes the new plot the selected one, so the next checkbox lands on it.
2. Unchecking a plot's last series removes the plot. A plot must hold at least one series (`parse_plots` refuses an empty one).
3. The drawer's glob is anchored: `pipe.T*` means what it means in a spec's `variables` list (the same `_pattern_to_regex` rule, brackets literal). With no `*` or `?` the filter is a substring. It matches a variable's name OR its label.
4. The drawer draws at most 200 top-level rows and says how many it is not showing ("Showing 200 of 1,843 rows — narrow the filter"). An opened family shows at most 50 members, then "… N more".
5. The Tests drawer has its own filter, always grouped with short names. The Suite page's chips and toggles stay on the Suite page.
6. Which drawer is open, and the Tests drawer's filter, are remembered per browser tab, so the drawer stays open across a switch of test.
7. **The folded CRITERIA row (3b's plan listed it for 3c) moves to 3d**, beside the expansion that writes one criterion per index: in 3c a family's criteria stay one row per member. The board drew a folded criteria row on plot 2 as decoration; 3c does not build it. **Owner ruling, 2026-09-25: it stays in 3d.**
8. A click anywhere in a plot card outside its plot area selects the plot. A criteria row's own click bubbles to its card and selects it; its swatch and its node controls (+ / wrap / −) stop the click in the browser (`plot_card.py`'s swatch, `actions.py`'s `InlineHost` column) and do not select, as they stopped it before 3c so that a click on them does not also open the row's editor. The plot area is left alone: it takes zooms and handle drags. That matches the owner's ruling (Q5 A): a click on a card's toolbar or margin selects it.
9. Remove plot obeys the same rule as the checkbox (Q7): it is grey, with the reason, when the plot is the last one showing a scored variable.
10. A layout edit refused by the Session (a job holds the test; a patch error) is a notification with the Session's sentence. Nothing else changes.

**Spike (2026-09-25, Chromium, NiceGUI 3.16, a throwaway page): facts this plan relies on.**
1. `ui.left_drawer(value=True).props("width=40 behavior=desktop bordered")`, its `width` prop set to 360 and back, PUSHES the page: a full-width `ui.plotly` went 1208 → 888 → 1208 px wide at a 1280 px viewport, and 828 → 508 → 828 at 900 px. `document.documentElement.scrollWidth` stayed equal to the viewport width both times (no horizontal scroll).
2. `ui.plotly` resizes itself: NiceGUI 3.16's `plotly.js` observes its element with a `ResizeObserver` and calls `Plotly.Plots.resize` unless the figure's `config.responsive` is `false`. The svg's `width` attribute followed the element each time. No resize code is needed.
3. `behavior=desktop` matters: below Quasar's 1024 px breakpoint a drawer otherwise turns into an overlay, which would cover the plots instead of narrowing them.
4. The header (`ui.header`) spans the full width above the drawer.

**Architecture — five moves.**
1. **The family rule** (`figures/families.py`): a multi-dimensional index refuses a family only AFTER the last single-integer index, and an expression may carry integer index lists (`[4, 1]`).
2. **Layout is data, edited by pure functions** (new `authoring/layout.py`, UI-free): the effective layout, add/remove series, new plot, remove plot, the stranded-variable rule, and the ONE op on `/plots`. `Session.stage` collapses a `replace` that follows an `add` or `replace` of the same path, and a `remove` that follows a `replace`, so a run of layout gestures is one pending op.
3. **The drawer's rows are data too** (new `authoring/variable_rows.py`, UI-free): grouping, folding, filtering, the cap, each row's state on the selected plot.
4. **The rail** (new `shell/rail.py`) hosts two drawers, `TestsDrawer` over a `TestList` extracted from the Suite page (new `shell/test_list.py`) and `VariablesDrawer` (new `shell/variables_drawer.py`). The Test page owns `selected`, `stage_layout`, and the rings.
5. **The header** gains a ⋮ menu; its Save caption counts new entries (`save_caption`, a pure function).

**Seams:** none of the pre-release block's surfaces moves: the CLI, `testing.json`, the reference-JSON layout and the `test_spec.json` format are unchanged. The tool WRITES the existing `plots` key; `parse_plots` already defines it.

**Not built here:** the slice field, save-time expansion and the folded criteria row (3d); moving plots and plot titles (ideas); the residuals waiting for the owner's look; any change to scoring.

**Review provenance.** The plan had a fresh-context EXECUTING review on 2026-09-25 (ACCEPT-WITH-FIXES: 1 HIGH, 11 MED, 5 LOW). Tasks 1–9 were built in a `git archive` copy of e7c2b6b, one commit per task. Every RED named for Tasks 1–8 was run and went red as stated, and every named mutation was applied: all were killed except the three in items 4, 5 and 6 below, which the corrections fix. Every `dstf author` subprocess was checked to import the archive. The archive ended at 2810 passed / 18 skipped (not playwright), 63 passed (playwright), with `ruff`, `format` and bare `mypy` (76 files) clean. Its diff, `.soul/plan3c-review.diff` (copied from the review's scratchpad), is a WORKING reference implementers may read per task; the plan and the block below stay the spec, and the diff's UI code is probe quality. NOT executed by the review: Task 10 (docs) and Task 11 (the gate). Every finding is in the block below, which wins where a task's text disagrees. **Not executed by anyone yet, and so the least-verified text here:** the parts of the block the orchestrator wrote beyond the review's findings. They are item 6's no-wrap assertion, item 8's title and plot-only fold, item 17's Remove-plot selection rule, the rail label fix with its assertion, and the `var-group:top-level` marker. Each gets its RED and its mutation run by the task's reviewer.

## Corrections from the executing review (binding on every task; where a task's text disagrees, this block wins)

1. **HIGH, Task 7: no Session read per card.** Each card calling `session.layout(...)` and `page.scored()` (which reads `tree_view`) broke `test_a_rebuild_reads_the_tree_and_the_tracked_names_once` (tree_view 5 against 0) and `test_the_page_costs_the_same_whether_it_has_five_cards_or_forty[5,40]` (variable_texts 7 and 42 against at most 2). `rebuild` reads `layout = session.layout(model_id)` and `scored = self.scored(view)` ONCE and hands both to `PlotCard(..., layout, scored)`. `TestPage.scored(view)` takes the page's view; it never reads one itself.
2. **MED, Task 2: an existing test moves.** `TestRescoreAndStage::test_consecutive_replaces_of_one_path_collapse` stages `LOOSEN` (an `add`) and then replaces of the same path, which now collapse into it: its counts become `n`, then `n + 2`, and it asserts the merged op equals `PatchOp(A, "add", <path>, <last value>)`. Its "a non-replace never collapses" line now sees a remove after a replace merge; rewrite that line to a remove after an ADD, which stays two ops. The plan's grep missed it because the `add` is inside the `LOOSEN` constant: grep for `LOOSEN` as well.
3. **MED, Task 2: `test_ops_on_other_paths_or_models_do_not_collapse` as written raises** (`replace /plots` on B, which has no key: PatchError "key 'plots' not present"). It stages `add /plots` for A, then `add /plots` for B, and expects two ops.
4. **MED, Task 7: mutation (c) survived.** Remove plot never passes `select`, so the restore path was never run. The refused-edit test also calls `page.stage_layout(page.shell.session.layout(A), select=6)` under the refusing stage and asserts it returns `False` and `page.selected` is unchanged.
5. **MED, Task 9: mutation (a) survived.** The pure test hands `save_caption` the loaded document, so a Header passing the EFFECTIVE document passed it. Add `test_save_names_a_new_entry` (simulation, `tests/test_shell.py`) over `_modelica_project` + `_add_bar`: stage `add /plots` for a model with no loaded entry; the `save` button reads `Save 1 spec edit (1 test, 1 new entry) to test_spec.json`. That kills (a).
6. **MED, Task 9: mutation (b) passed the 900 px browser test**, because the header's buttons wrap their text and still fit. Mutation (b) is killed by the menu simulation test (its ancestor check); name that test for (b). The browser test additionally asserts that EVERY child of `.dstf-header-row` has its right edge ≤ `innerWidth` and that no header button's label wraps to a second line (its height ≤ 1.5 × its line height). **Owner ruling, 2026-09-25, amending this:** at 900 px the pending count, Save and Discard MAY wrap to two lines (the real header also carries the backend select, the run indicator and Save's why-sentence, and one line needs ~950 px); nothing may be cut off. The no-wrap assertion holds at 1280 px. Hiding Save's why-sentence while nothing is pending and dropping the centre indicator go to the owner's hands-on-look list.
7. **MED, Task 9: existing tests that move, named.**
   - `test_save_discard_and_export_say_their_object_effect_and_count`: a `ui.menu_item` has no `.text`. The Export item's caption is its own `ui.item_label` child set with `set_text`, and the test reads that label.
   - `test_the_header_is_one_row_unless_a_page_asks_for_a_second`: `stop` is no longer in the row; it checks `header-menu` instead.
   - The Suite browser test's `get_by_role("button", name="Stop server")`: click `.dstf-header-menu` first, then `page.locator(".q-menu").get_by_text("Stop server")`. The item is not role `menuitem`.
   - The user simulation DOES find and click items inside a closed `ui.menu`, so the other simulation tests of export / stop / other-backend stay unchanged.
8. **MED, Task 8: a plot's title and its plot-only line fold families, as the report's already do.** The plan said card 0's title would name the family; the probe's title read `x, z, pipe.T[1], pipe.T[2], pipe.T[3]` while the legend and the drawer folded. The report's plot section already folds its title and its plot-only lines with `group_families` + `family_label` (`reporting/report_suite.py`, the `"title"` and `"plot_only_lines"` keys), so the tool follows the report ("both tiers draw the same plots"). This is not a new design. `PlotCard` builds its title, its muted names line and its plot-only line the same way over its `labels` / `texts`. Test: after adding the pipe family, `plot-title:0` reads `x, z, pipe.T[1:3]` and `plot-only:0` names `z, pipe.T[1:3]`.
9. **MED, Task 6: `.dstf-row-current` lost to the status tints** (measured rgb(255,235,238), the fail tint). Write it as `.dstf-row.dstf-row-current` and emit it after the tints; it then measured `#e3f2fd`.
10. **MED, Task 6 browser: the width settles after the plot does.** `scrollWidth == innerWidth` read as soon as the plot's width landed was 1551 at 1280 px, and it settled about 0.8 s later. Assert it with `page.wait_for_function`, never a single read.
11. **MED, Task 4: a stronger mutant (b) survived** (`scored.get(m, 0)` in place of the membership test: the plan's test killed the plain one only by a KeyError). Add `assert _groups(selected=1)[1].rows[0].stranded == ()` and `assert tracked["x"].criteria == 1`.
12. **MED, Task 4: the group title stops at a bracket.** `_group_title` returned `"port[2, 1]"`, so the plan's own group test failed. It is `head.split("[", 1)[0] if dot else TOP_LEVEL`. Known and left (LOW): `der(pipe.T[1])` groups as `der(pipe`; a result file's `der(...)` names are grouped by their text, and nothing in 3c needs better.
13. **LOW, Task 5: `SuitePage` keeps a `rows` property** delegating to `TestList.rows`: the Suite tests read `page.rows[...]` about thirty times, and they stay unedited only through it.
14. **LOW, Task 6 simulation: `user.find` sees visible elements only.** A hidden panel is asserted with `_none(user, "drawer:tests")`, not by reading `.visible`.
15. **LOW, Task 7: `test_reset_layout_…` is two tests**, one per fixture: two fixtures writing into one `tmp_path` raised FileExistsError.
16. **LOW, Task 8 browser:**
   - Scroll the new card into view (`cards.nth(2).scroll_into_view_if_needed()`) before reading it: a card draws on scroll.
   - Read SVG text with `textContent` (`inner_text` returns None).
   - The follow test does not see mutation (a): the fixture's y is refused as stranded there, so nothing is staged. Other tests kill (a); say so in the test's docstring.
17. **LOW, facts for the builder:**
   - NiceGUI splits markers on whitespace, so `var-group:(top level)` is two markers. Lookups still work because the filter splits the same way; the group's marker is `var-group:top-level` anyway, for readability.
   - The user simulation's click calls every click listener and ignores `js_handler`.
   - **Remove plot's click stops propagation** (`js_handler="(e) => { e.stopPropagation(); emit(); }"`): bubbling to its card selected it, and after the rebuild the ring sat on whichever card moved into the removed slot. `stage_layout` for a removal keeps the selection on the SAME plot: a removed index below `selected` shifts it down by one; removing the selected plot selects the one now at its index (the last, if it was the last).
   - Task 9 needs `from dstf.authoring.spec_document import PatchOp` in `tests/test_shell.py`, and `Header.refresh`'s now-unused `models` goes (ruff F841).
   - **The rail's labels clipped at 40 px** ("Tes", "Va"; screenshot read). The rail's buttons carry no horizontal padding and their labels are 10 px with no letter-spacing. A browser assertion checks that each label's `scrollWidth <= clientWidth`. The owner ruled a LABELLED rail, so a clipped label is a defect, not a style.
   - `tests/test_authoring_imports.py`'s PROBE gains `dstf.authoring.layout` and `dstf.authoring.variable_rows`: both import without NiceGUI.

## Conventions for every task

The "Conventions for every task" block of `docs/superpowers/plans/2026-09-20-the-test-page.md` holds verbatim: gates and how to read them; red before green, shown; named mutations run in a `git archive` copy with an ABSOLUTE exported `PYTHONPATH`, `PYTHONDONTWRITEBYTECODE=1`, and the module's `__file__` printed and ASSERTED under the archive first; commit by path with `-F <own msg file>`, never `--amend`, never `git stash`, not even inside a one-liner; never run `dstf` against `testing_output/` or a real work dir; done-checks are predicates; locate code by content; no internal record id in a tracked file; `.soul/` untouched. So do its "Corrections" items 2, 3, 6 and 8 where they still apply (the user simulation's rules; mypy/ruff idioms; the review harness; the NiceGUI facts), and the 2026-09-24 plan's Corrections item 2: a fixture a CI-run module needs lives in `tests/test_session.py`, never in a Playwright module. `docs/decisions.md` gains exactly one entry, D134, in Task 10. `docs/ideas.md` is touched only in Task 10. Additionally:

- **Never touch `/mnt/d/Modelica/TRANSFORM-UnitTests` or `/mnt/d/Modelica/TRANSFORM-Library`.** Task 11 is the orchestrator's and works over a copy.
- **Every UI task LOOKS.** The implementer takes a viewport screenshot of what the task changed, makes a thumbnail with `render_check.py thumb`, and READS it. Where text enters a Plotly template, a real-mouse Playwright read of `.hoverlayer text` asserts it (the figure's `x unified` box has no `.hovertext`).
- **A design change seen in a LOOK is not fixed in passing.** An implementer who sees something the plan did not foresee stops and reports it.
- **The user simulation's click does not bubble to parents.** A behaviour that depends on bubbling (the card click that selects a plot; a row's hover button) is asserted in Chromium.
- Each task leaves the whole suite green (`uv run pytest -m "not playwright"`, and `uv run pytest -m playwright` for Tasks 6, 7, 8, 9), with `ruff check`, `ruff format --check` and bare `mypy` clean. `[tool.mypy] files` lists `src/dstf/authoring` as a whole, so every new module of this plan is type-checked with no change to `pyproject.toml`.

---

### Task 1 — The family rule: a comma index before the last one folds

**Files:** `src/dstf/figures/families.py`; tests `tests/test_plot_figure.py`.

- [ ] **RED** — in `tests/test_plot_figure.py`, extend `test_family_of_is_the_last_integer_index`'s parameters with:

```python
        ("massPorts[4, 1].C[3]", ("massPorts[4, 1].C[]", 3)),
        ("a[1,2].b[7]", ("a[1,2].b[]", 7)),
        ("a[1].b[2,3]", None),
```

  and add:

```python
def test_an_expression_may_index_with_an_integer_list_before_its_last_index():
    """TRANSFORM's UnitTests expressions index ports two-dimensionally and the
    species last: `pipe_single.massPorts[4, 1].C[1]` is element 1 of an
    array, and `C[1..3]` of one port fold (3c ruling). A slice or a
    reduction is still no element."""
    assert family_of("pipe_single.massPorts[4, 1].C[1]", expression=True) == (
        "pipe_single.massPorts[4, 1].C[]",
        1,
    )
    assert family_of("a[1, :].b[2]", expression=True) is None
    assert family_of("sum(core.Qs[6, :])", expression=True) is None
    labels = {N1: "p.m[4, 1].C[1]", N2: "p.m[4, 1].C[2]"}
    (key, members) = group_families([N1, N2], labels)[0]
    assert members == [N1, N2]
    from dstf.figures.families import family_label

    assert family_label(key, members, labels) == "p.m[4, 1].C[1:2]"
```

  Replace `test_family_of_answers_a_name_exactly_as_it_did_before_labels` with a differential that proves the ONE ruled change:

```python
def _family_of_after_3c(name: str):
    """`_family_of_at_2ac8c8c` with the one change 3c ruled: a
    multi-dimensional index refuses a family only when it comes AFTER the
    last single-integer index. Copied, not imported."""
    import re

    m = re.compile(r"\[(\d+)\](?!.*\[\d+\])").search(name)
    if m is None:
        return None
    if re.compile(r"\[[^\]]*,[^\]]*\]").search(name, m.end()):
        return None
    return name[: m.start()] + "[]" + name[m.end() :], int(m.group(1))


def test_family_of_over_names_changed_only_where_3c_ruled():
    """A differential over generated names, not a hand-list (the b147918
    review fuzzed 300k names against 2ac8c8c's copy). The current rule
    agrees with 2ac8c8c's everywhere except names whose multi-dimensional
    index precedes the last single one — and those it now folds."""
    import re

    names = _generated_result_file_names(25000)
    assert len(set(names)) > 1000
    assert [n for n in names if family_of(n) != _family_of_after_3c(n)] == []
    changed = [n for n in names if _family_of_after_3c(n) != _family_of_at_2ac8c8c(n)]
    assert changed  # the generator does produce the ruled shape
    multi = re.compile(r"\[[^\]]*,[^\]]*\]")
    for n in changed:
        assert _family_of_at_2ac8c8c(n) is None
        key, _ = family_of(n)
        assert multi.search(key[: key.index("[]")])
```

  Run: `uv run pytest tests/test_plot_figure.py -q`. Expected: the new parameters, the expression test and the differential FAIL on unchanged code. Show it.
- [ ] **GREEN** — in `families.py`:
  - After `_QUOTED_ID`, add:

```python
#: An index of integers only, one or several (`[3]`, `[4, 1]`): a position
#: in an array, however many dimensions. Collapsed before `_PLAIN_NAME`, so
#: an expression that indexes a port two-dimensionally is still a plain name
#: (`massPorts[4, 1].C[1]`). A slice (`[6, :]`) is not one: it stays and
#: refuses the name.
_INT_INDEX = re.compile(r"\[\s*\d+(?:\s*,\s*\d+)*\s*\]")
```

  - In `_names_an_element`, the last line becomes `return bool(_PLAIN_NAME.match(_INT_INDEX.sub("[0]", _QUOTED_ID.sub("q", text))))`.
  - In `family_of`, replace the `_MULTI_INDEX.search(text)` guard that precedes `_LAST_INDEX` with a check AFTER the match:

```python
    m = _LAST_INDEX.search(text)
    if m is None:
        return None
    # Multi-dimensional AFTER the last single index (`a[1].b[2,3]`): the
    # element's own position is two-dimensional, so it is no member of an
    # `a` array. BEFORE it (`massPorts[4, 1].C[3]`) it is where the array
    # sits, and the element is C's 3rd (3c ruling).
    if _MULTI_INDEX.search(text, m.end()):
        return None
    return text[: m.start()] + "[]" + text[m.end() :], int(m.group(1))
```

  - The module docstring's first paragraph and `family_of`'s docstring say the new rule in one sentence each: "``x[2,3]`` does not" stays; add "``massPorts[4, 1].C[3]`` does, by its last index".
- [ ] **Mutations:** (a) restore the whole-text `_MULTI_INDEX.search(text)` guard; (b) drop the after-match guard; (c) leave `_INT_INDEX` out of `_names_an_element`. Each fails a named test.
- [ ] **Commit:** `families: a comma index before the last one folds (3c)`.

### Task 2 — Session: consecutive ops on one path collapse

**Files:** `src/dstf/authoring/session.py` (`Session.stage`, the "Convention 8" block); tests `tests/test_session.py`.

Why: every layout gesture stages the whole `/plots` list. Without this, five clicks read "5 spec edits" and the pending drawer lists five full layouts. The existing rule collapses `replace` after `replace`. Two more cases are safe in RFC 6902 for any path, object member or array index:
- `replace` after `add` of the same path is the `add` of the new value.
- `remove` after `replace` of the same path is the `remove`: `replace` needed the member to exist, and `remove` then takes it.

`remove` after `add` is NOT collapsed. When the member existed before, `add` acted as a replace, and dropping both would leave the member in place instead of removing it.

- [ ] **RED** — `class TestStageCollapses` in `tests/test_session.py`, over the `session` fixture's project (A has an entry and no `plots` key):
  - `test_a_replace_after_an_add_of_one_path_is_one_add`: stage `PatchOp(A, "add", "/plots", L1)`, then `PatchOp(A, "replace", "/plots", L2)`. Expect `session.spec.pending == [PatchOp(A, "add", "/plots", L2)]` and `session.spec.entry(A)["plots"] == L2`.
  - `test_a_remove_after_a_replace_is_one_remove`: write a spec where A's entry HAS `plots: L1` (the helper writes the file before `open`). Stage `replace /plots L2`, then `remove /plots`. Expect `pending == [PatchOp(A, "remove", "/plots")]` and no `plots` in the entry.
  - `test_a_remove_after_an_add_is_not_collapsed`: same file with `plots: L1`. Stage `PatchOp(A, "add", "/plots", L2)` (it acts as a replace), then `remove`. Expect TWO pending ops and no `plots` in the effective entry. This is the case where dropping both would have left `L1`.
  - `test_ops_on_other_paths_or_models_do_not_collapse`: `add /plots` for A then `replace /plots` for B leaves two ops.
  - `L1 = [{"kind": "time", "series": [{"var": "x"}]}]`, `L2 = [{"kind": "time", "series": [{"var": "x"}, {"var": "y"}]}]`.
  Expected on unchanged code: the first two FAIL (two ops each), the last two pass. Show it.
- [ ] **GREEN** — replace the Convention 8 block in `stage` with:

```python
        # Convention 8: an edit that lands on the path the last pending op
        # wrote supersedes it, so the drawer and the envelope read "value ->
        # new value", never a keystroke log: a replace after an add or a
        # replace of the same path is that op with the new value, and a
        # remove after a replace is the remove. A remove after an ADD is
        # kept as two: when the member existed, the add replaced it, and
        # dropping both would leave it. A batch is never collapsed: its ops
        # belong together.
        pending = list(self.spec.pending)
        if len(staged) == 1 and pending:
            new, last = staged[0], pending[-1]
            if (new.model_id, new.path) == (last.model_id, last.path):
                if new.op == "replace" and last.op in ("add", "replace"):
                    pending.pop()
                    staged = [PatchOp(new.model_id, last.op, new.path, new.value)]
                elif new.op == "remove" and last.op == "replace":
                    pending.pop()
```

- [ ] **Existing tests that move:** any test that stages `add` then `replace` on one path and counts two ops. Find them with `grep -n '"add"' tests/test_session.py tests/test_builtin_capabilities.py tests/test_test_page.py` and update each, saying why in the test.
- [ ] **Mutations:** (a) `last.op in ("replace",)` only; (b) collapse `remove` after `add` too. Each fails a named test.
- [ ] **Commit:** `session: a replace after an add, and a remove after a replace, of one path collapse`.

### Task 3 — `authoring/layout.py`: the layout and its one op

**Files:** create `src/dstf/authoring/layout.py`; modify `src/dstf/authoring/session.py` (add `Session.layout`); tests `tests/test_layout.py` (new), `tests/test_session.py`.

- [ ] **RED** — `tests/test_layout.py`:

```python
"""The plot layout as the tool edits it (3c): pure functions over the
`plots` key's PlotSpecs, and the ONE op on `/plots` a gesture stages."""

from __future__ import annotations

from dstf.authoring.layout import (
    add_series,
    current_layout,
    layout_op,
    new_plot,
    plots_showing,
    remove_plot,
    remove_series,
    reset_op,
    stranded,
)
from dstf.authoring.spec_document import PatchOp
from dstf.discovery.plots import PlotSpec, SeriesSpec, parse_plots


def _l(*plots):
    return tuple(PlotSpec(series=tuple(SeriesSpec(v) for v in p)) for p in plots)


def test_no_plots_key_is_one_plot_per_tracked_variable():
    assert current_layout(None, ["x", "y"]) == _l(["x"], ["y"])
    assert current_layout(_l(["x", "y"]), ["x", "y"]) == _l(["x", "y"])
    assert current_layout((), ["x"]) == ()


def test_add_series_appends_what_the_plot_lacks_in_order():
    assert add_series(_l(["x"], ["y"]), 0, ["z", "x", "z"]) == _l(["x", "z"], ["y"])


def test_removing_a_plots_last_series_removes_the_plot():
    assert remove_series(_l(["x", "z"], ["y"]), 0, ["z"]) == _l(["x"], ["y"])
    assert remove_series(_l(["x"], ["y"]), 0, ["x"]) == _l(["y"])


def test_a_new_plot_goes_below_the_selected_one_and_says_where():
    layout, at = new_plot(_l(["x"], ["y"]), 0, ["z"])
    assert (layout, at) == (_l(["x"], ["z"], ["y"]), 1)
    assert new_plot((), None, ["z"]) == (_l(["z"]), 0)


def test_remove_plot():
    assert remove_plot(_l(["x"], ["y"]), 0) == _l(["y"])


def test_stranded_names_the_scored_variables_a_change_takes_off_every_plot():
    before = _l(["x"], ["x", "y"], ["z"])
    assert stranded(before, remove_plot(before, 0), ["x", "y"]) == []
    assert stranded(before, remove_plot(before, 1), ["x", "y"]) == ["y"]
    # a scored variable no plot showed before is not this change's doing
    assert stranded(_l(["x"]), (), ["x", "q"]) == ["x"]
    assert plots_showing(before, "x") == (0, 1)


def test_the_op_is_add_without_a_plots_key_and_replace_with_one():
    layout = _l(["x", "z"])
    raw = [{"kind": "time", "series": [{"var": "x"}, {"var": "z"}]}]
    assert layout_op("M", {"model": "M"}, layout) == PatchOp("M", "add", "/plots", raw)
    assert layout_op("M", None, layout) == PatchOp("M", "add", "/plots", raw)
    assert layout_op("M", {"model": "M", "plots": []}, layout) == PatchOp(
        "M", "replace", "/plots", raw
    )
    assert parse_plots(raw) == layout
    assert reset_op("M") == PatchOp("M", "remove", "/plots")
```

  Plus in `tests/test_session.py`, `TestPlots`: `test_layout_is_the_effective_plots_key_else_one_per_tracked` over `_plots_project`: `s.layout(A)` is one plot per tracked name before any op; after `s.stage([PatchOp(A, "add", "/plots", LAYOUT)])` it is `parse_plots(LAYOUT)`, which keeps a series the result lacks (the layout is what the spec says; `plots()` is what a page draws). Expected: ImportError / AttributeError on unchanged code. Show it.
- [ ] **GREEN** — `src/dstf/authoring/layout.py`:

```python
"""A test's plot layout as the tool edits it (the roadmap's 3c; D134): the
`plots` key's list, changed one gesture at a time and staged as ONE op on
`/plots` — `Session.stage` collapses a run of them (Convention 8). UI-free:
the Variables drawer, a plot card's toolbar and the tree card's Reset
layout all call in, so the rules below hold on every surface at once."""

from __future__ import annotations

from collections.abc import Sequence

from ..discovery.plots import PlotSpec, SeriesSpec, default_plots, plots_to_raw
from .spec_document import PatchOp

Layout = tuple[PlotSpec, ...]


def current_layout(plots: Sequence[PlotSpec] | None, tracked: Sequence[str]) -> Layout:
    """The layout a gesture edits: the test's ``plots`` key, else one plot
    per tracked variable — the default both tiers draw. The first gesture on
    a test without the key writes that default out with its change."""
    return tuple(plots) if plots is not None else default_plots(tracked)


def _with(layout: Layout, index: int, plot: PlotSpec) -> Layout:
    return layout[:index] + (plot,) + layout[index + 1 :]


def add_series(layout: Layout, index: int, names: Sequence[str]) -> Layout:
    """Plot ``index`` with ``names`` appended, in order, minus those it has."""
    plot = layout[index]
    have = {s.var for s in plot.series}
    added = tuple(SeriesSpec(n) for n in dict.fromkeys(names) if n not in have)
    return _with(layout, index, PlotSpec(series=plot.series + added, kind=plot.kind))


def remove_series(layout: Layout, index: int, names: Sequence[str]) -> Layout:
    """Plot ``index`` without ``names``; a plot left with no series goes —
    a plot is at least one series (`parse_plots` refuses an empty one)."""
    drop = set(names)
    plot = layout[index]
    kept = tuple(s for s in plot.series if s.var not in drop)
    if not kept:
        return remove_plot(layout, index)
    return _with(layout, index, PlotSpec(series=kept, kind=plot.kind))


def new_plot(
    layout: Layout, after: int | None, names: Sequence[str]
) -> tuple[Layout, int]:
    """A plot of ``names`` below plot ``after`` (at the end when None), and
    its index — the page selects it, so the next checkbox lands on it."""
    at = len(layout) if after is None else after + 1
    plot = PlotSpec(series=tuple(SeriesSpec(n) for n in dict.fromkeys(names)))
    return layout[:at] + (plot,) + layout[at:], at


def remove_plot(layout: Layout, index: int) -> Layout:
    return layout[:index] + layout[index + 1 :]


def plots_showing(layout: Layout, name: str) -> tuple[int, ...]:
    return tuple(
        i for i, plot in enumerate(layout) if any(s.var == name for s in plot.series)
    )


def stranded(before: Layout, after: Layout, scored: Sequence[str]) -> list[str]:
    """The scored variables ``before`` shows on some plot and ``after`` on
    none (the owner's ruling, 2026-09-25): a criterion's row sits under the
    plot that shows its variable, and with no such plot the row has nowhere
    to go. The tool refuses such a change and says which variables."""
    return [
        name
        for name in scored
        if plots_showing(before, name) and not plots_showing(after, name)
    ]


def layout_op(model_id: str, entry: dict | None, layout: Layout) -> PatchOp:
    """The ONE op a gesture stages: ``add`` when the effective entry has no
    ``plots`` key (or no entry: the op creates it), ``replace`` when it has."""
    verb = "replace" if entry is not None and "plots" in entry else "add"
    return PatchOp(model_id, verb, "/plots", plots_to_raw(layout))


def reset_op(model_id: str) -> PatchOp:
    """Reset layout: the key goes, and the test draws one plot per tracked
    variable again."""
    return PatchOp(model_id, "remove", "/plots")
```

  `Session.layout`, beside `plots`:

```python
    def layout(self, model_id: str) -> tuple[PlotSpec, ...]:
        """The layout a gesture edits (`authoring.layout`): the effective
        ``plots`` key with pending ops applied, else one plot per tracked
        variable. Unlike :meth:`plots` it keeps a series the result lacks —
        it is what the spec says, not what a page can draw."""
        test = self._require_project().test(model_id)
        if test is None:
            raise KeyError(model_id)
        return current_layout(test.plots, self.tracked(model_id))
```

- [ ] **Mutations:** (a) `remove_series` returns the empty plot instead of dropping it; (b) `layout_op` always `add`; (c) `stranded` counts a scored name `before` did not show. Each fails a named test.
- [ ] **Commit:** `layout: the plot layout as pure functions and one op on /plots`.

### Task 4 — `authoring/variable_rows.py`: the drawer's rows as data

**Files:** create `src/dstf/authoring/variable_rows.py`; tests `tests/test_variable_rows.py` (new).

The rows the Variables drawer draws, computed without NiceGUI: every variable of the last result, grouped (Tracked first, then by the first dot component of the NAME, "(top level)" for a name with no dot, Diagnostics last), families folded over LABELS (`group_families`), filtered, capped, and each row's state against the selected plot.

- [ ] **RED** — `tests/test_variable_rows.py`:

```python
"""The Variables drawer's rows (3c; DESIGN.md "The Variables drawer"): the
groups, the folds, the filter, the cap, and each row's state on the
selected plot — computed without a page."""

from __future__ import annotations

import re

import pytest

from dstf.authoring.variable_rows import cap, matcher, variable_groups
from dstf.discovery.plots import PlotSpec, SeriesSpec

NAMES = [
    "x", "y", "a0", "z",
    "pipe.T[1]", "pipe.T[2]", "pipe.T[3]",
    "port[2, 1].C[1]", "port[2, 1].C[2]",
    "CPUtime",
]


def _l(*plots):
    return tuple(PlotSpec(series=tuple(SeriesSpec(v) for v in p)) for p in plots)


def _groups(**kw):
    args = dict(
        names=NAMES,
        tracked=["x", "y"],
        diagnostics=["CPUtime", "EventCounter"],
        labels={},
        layout=_l(["x"], ["y", "pipe.T[1]"]),
        selected=0,
        scored={"x": 1, "y": 2},
        keep=lambda text: True,
    )
    args.update(kw)
    return variable_groups(**args)


def test_groups_are_tracked_then_by_prefix_then_top_level_then_diagnostics():
    assert [g.title for g in _groups()] == [
        "Tracked", "pipe", "port", "(top level)", "Diagnostics",
    ]


def test_a_family_is_one_row_over_its_members():
    pipe = _groups()[1]
    (row,) = pipe.rows
    assert row.members == ("pipe.T[1]", "pipe.T[2]", "pipe.T[3]")
    assert row.text == "pipe.T[1:3]"
    port = _groups()[2].rows[0]
    assert port.text == "port[2, 1].C[1:2]"


def test_a_row_knows_the_selected_plot_and_the_others():
    tracked = {r.members[0]: r for r in _groups()[0].rows}
    assert tracked["x"].on_selected == 1 and tracked["x"].elsewhere == ()
    assert tracked["y"].on_selected == 0 and tracked["y"].elsewhere == (1,)
    pipe = _groups()[1].rows[0]
    assert (pipe.on_selected, pipe.elsewhere) == (0, (1,))


def test_a_scored_variable_on_its_last_plot_is_stranded_there():
    tracked = {r.members[0]: r for r in _groups()[0].rows}
    assert tracked["x"].stranded == ("x",)  # plot 0 is x's only plot
    assert tracked["y"].stranded == ()  # y is not on plot 0 at all
    moved = {r.members[0]: r for r in _groups(selected=1)[0].rows}
    assert moved["y"].stranded == ("y",)


def test_no_selected_plot_means_nothing_on_it():
    rows = _groups(layout=(), selected=None)[0].rows
    assert all(r.on_selected == 0 and r.stranded == () for r in rows)


def test_labels_are_read_and_the_name_rides_beside():
    labels = {"x": "a.Q"}
    row = _groups(labels=labels)[0].rows[0]
    assert (row.text, row.name) == ("a.Q", "x")
    assert _groups()[0].rows[0].name is None


def test_the_filter_keeps_members_and_folds_over_what_it_kept():
    keep = matcher("pipe.T[?]", regex=False, case=False)
    groups = _groups(keep=keep)
    assert [g.title for g in groups] == ["pipe"]
    keep2 = matcher("T[2]", regex=False, case=False)
    (row,) = _groups(keep=keep2)[0].rows
    assert row.members == ("pipe.T[2]",) and row.text == "pipe.T[2]"


@pytest.mark.parametrize(
    ("needle", "regex", "case", "text", "hit"),
    [
        ("pipe", False, False, "PIPE.t[1]", True),  # substring, any case
        ("pipe", False, True, "PIPE.t[1]", False),
        ("pipe.T[*]", False, False, "pipe.T[12]", True),  # brackets literal
        ("pipe.T*", False, False, "a.pipe.T[1]", False),  # a glob is anchored
        ("^port", True, False, "port[2, 1].C[1]", True),
        ("", False, False, "anything", True),
    ],
)
def test_matcher(needle, regex, case, text, hit):
    assert matcher(needle, regex=regex, case=case)(text) is hit


def test_a_bad_regex_raises_for_the_drawer_to_say_so():
    with pytest.raises(re.error):
        matcher("(", regex=True, case=False)


def test_cap_counts_top_level_rows_and_says_what_it_left_out():
    many = [f"v{i}" for i in range(250)]
    groups = _groups(names=many, tracked=[], layout=(), selected=None, scored={})
    kept, shown, total = cap(groups, 200)
    assert (shown, total) == (200, 250)
    assert sum(len(g.rows) for g in kept) == 200
```

  (The layout of `NAMES` is deliberate for `ruff format`: it may reflow the list; that is fine.) Expected: ImportError on unchanged code. Show it.
- [ ] **GREEN** — `src/dstf/authoring/variable_rows.py`:

```python
"""The Variables drawer's rows (the roadmap's 3c; DESIGN.md "The Variables
drawer"), as data: the page draws them and knows none of the rules.

Every variable of the last result, in groups — the tracked ones first, then
by the first dot component of the NAME ("(top level)" for a name with
none), the diagnostics last — with arrays folded into one row over their
LABELS (`figures.families`, the one family rule both tiers use), filtered,
and each row's state against the selected plot."""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass

from ..backends.types import _pattern_to_regex, is_glob_pattern
from ..discovery.plots import PlotSpec
from ..figures.families import family_label, group_families
from .layout import plots_showing

TOP_LEVEL = "(top level)"


@dataclass(frozen=True)
class VarRow:
    key: str  #: the family key, or the name of a lone variable
    members: tuple[str, ...]  #: names, by index; one for a lone variable
    text: str  #: what it reads as: the family label, or the label
    name: str | None  #: the muted name beside ``text``, when they differ
    on_selected: int  #: members the selected plot shows
    elsewhere: tuple[int, ...]  #: other plots showing any member, 0-based
    #: members a removal from the selected plot would leave on no plot while
    #: a criterion scores them (the owner's ruling): the row refuses it.
    stranded: tuple[str, ...]
    criteria: int  #: criteria on the stranded members, for the sentence


@dataclass(frozen=True)
class VarGroup:
    title: str
    rows: tuple[VarRow, ...]


def matcher(needle: str, *, regex: bool, case: bool) -> Callable[[str], bool]:
    """What the filter box keeps. A regex (``.*`` on) searches; a glob (a
    ``*`` or ``?`` in it) matches the WHOLE text with ``[ ]`` literal — the
    rule a spec's ``variables`` list uses, so what you type here means what
    it means there; anything else is a substring. ``re.error`` for a bad
    regex, for the drawer to say."""
    flags = 0 if case else re.IGNORECASE
    if not needle:
        return lambda text: True
    if regex:
        pattern = re.compile(needle, flags)
        return lambda text: pattern.search(text) is not None
    if is_glob_pattern(needle):
        glob = re.compile(_pattern_to_regex(needle).pattern, flags)
        return lambda text: glob.fullmatch(text) is not None
    folded = needle if case else needle.lower()
    return lambda text: folded in (text if case else text.lower())


def _group_title(name: str, tracked: set[str], diagnostics: set[str]) -> str:
    if name in tracked:
        return "Tracked"
    if name in diagnostics:
        return "Diagnostics"
    head, dot, _ = name.partition(".")
    return head if dot else TOP_LEVEL


def variable_groups(
    *,
    names: Sequence[str],
    tracked: Sequence[str],
    diagnostics: Collection[str],
    labels: Mapping[str, str],
    layout: Sequence[PlotSpec],
    selected: int | None,
    scored: Mapping[str, int],
    keep: Callable[[str], bool],
) -> list[VarGroup]:
    """``names`` in the drawer's groups. ``labels`` maps a name to what it
    reads as (absent: the name); ``scored`` maps a scored name to its number
    of criteria; ``keep`` is the filter, asked of the name AND the label."""
    tracked_set, diag = set(tracked), set(diagnostics)
    kept = [
        n for n in names if keep(n) or (n in labels and keep(labels[n]))
    ]
    by_title: dict[str, list[str]] = {}
    for name in kept:
        by_title.setdefault(_group_title(name, tracked_set, diag), []).append(name)
    middle = sorted(t for t in by_title if t not in ("Tracked", TOP_LEVEL, "Diagnostics"))
    order = ["Tracked", *middle, TOP_LEVEL, "Diagnostics"]
    layout = tuple(layout)
    showing = {n: plots_showing(layout, n) for n in kept}
    out: list[VarGroup] = []
    for title in order:
        members_of = by_title.get(title)
        if not members_of:
            continue
        rows = []
        for key, members in group_families(members_of, labels):
            text = family_label(key, members, labels)
            lone = members[0] if len(members) == 1 else None
            on = [m for m in members if selected is not None and selected in showing[m]]
            elsewhere = sorted(
                {i for m in members for i in showing[m] if i != selected}
            )
            stuck = tuple(m for m in on if m in scored and showing[m] == (selected,))
            rows.append(
                VarRow(
                    key=key,
                    members=tuple(members),
                    text=text,
                    name=lone if lone is not None and text != lone else None,
                    on_selected=len(on),
                    elsewhere=tuple(elsewhere),
                    stranded=stuck,
                    criteria=sum(scored[m] for m in stuck),
                )
            )
        out.append(VarGroup(title, tuple(rows)))
    return out


def cap(groups: list[VarGroup], limit: int) -> tuple[list[VarGroup], int, int]:
    """At most ``limit`` rows, groups in order: ``(groups, shown, total)``.
    A result of thousands of names drew thousands of rows otherwise."""
    total = sum(len(g.rows) for g in groups)
    out, left = [], limit
    for g in groups:
        if left <= 0:
            break
        out.append(VarGroup(g.title, g.rows[:left]))
        left -= len(out[-1].rows)
    return out, min(total, limit), total
```

  Note: `group_families` sorts members by index and orders groups by first appearance; the Tracked group is therefore in `tracked`'s order only if `names` lists the tracked first, which `Session.variable_names` does.
- [ ] **Mutations:** (a) a glob matched with `search` instead of `fullmatch`; (b) `stranded` ignores `scored`; (c) the filter asks only the name, not the label (add the case to `test_labels_are_read_and_the_name_rides_beside`: `keep=matcher("a.Q", …)` keeps `x`). Each fails a named test.
- [ ] **Commit:** `variable_rows: the Variables drawer's rows as data`.

### Task 5 — Refactor: the Suite page's list becomes `TestList`

**Files:** create `src/dstf/authoring/shell/test_list.py`; modify `src/dstf/authoring/shell/suite.py`. No new test: this task changes no behaviour, and the Suite page's existing tests (`tests/test_suite_page.py`, `tests/test_author_suite_playwright.py`) are its proof. A refactor, in its own commit.

- [ ] Move `Row`, `_package`, `_name`, `SuitePage.rebuild`'s row-building loop, `_paint` and `_apply_filter`'s row part into `class TestList` in `test_list.py`:

```python
class TestList:
    """The tests as rows grouped by package (brief §3.2; DESIGN.md "Tables"):
    the Suite page's list, with checkboxes, and the Tests drawer's, without
    (the owner's ruling, 2026-09-25). A row's verdict repaints in place; the
    list is rebuilt only when the test set or the layout moves."""

    def __init__(
        self,
        shell: Shell,
        *,
        on_open: Callable[[str], None],
        checked: set[str] | None = None,
        on_check: Callable[[str, bool], None] | None = None,
        current: str | None = None,
        marker: str = "",
    ) -> None: ...
    def rebuild(self, ids: Sequence[str], *, grouped: bool, short: bool) -> None: ...
    def paint(self, model_id: str) -> None: ...
    def filter(self, needle: str, statuses: set[str]) -> None: ...
    def shown(self) -> list[str]: ...
```

  `checked is None` builds no checkbox (the drawer). `current` gets `dstf-row-current` on its row. `marker` prefixes every marker the list sets (`""` keeps the Suite page's markers exactly: `tests`, `group:<g>`, `check:<id>`, `<id>`). `Row.checkbox` becomes `ui.checkbox | None`. The Suite page keeps its filters, chips, actions and handlers and calls the list.
- [ ] Run the Suite page's tests unchanged: `uv run pytest tests/test_suite_page.py -q` and `uv run pytest tests/test_author_suite_playwright.py -q`. Both pass with NO test edited. That is this task's done-check.
- [ ] **Commit:** `suite: the test list becomes TestList (refactor, no behaviour change)`.

### Task 6 — The rail and the Tests drawer

**Files:** create `src/dstf/authoring/shell/rail.py`; modify `src/dstf/authoring/shell/test_page.py`, `src/dstf/authoring/shell/tabstate.py`, `src/dstf/authoring/shell/style.py`; tests `tests/test_test_page.py`, `tests/test_author_test_page_playwright.py`.

- [ ] **RED (simulation)** — in `tests/test_test_page.py`, `class TestRail`:
  - `test_the_rail_opens_one_drawer_at_a_time`: open `/test/{A}`. Markers `rail:tests` and `rail:variables` exist, and neither drawer panel is visible (`drawer:tests`, `drawer:variables` hidden). Click `rail:tests`: the tests panel is visible. Click `rail:variables`: the variables panel is visible and the tests panel hidden. Click `rail:variables` again: both hidden.
  - `test_the_tests_drawer_lists_the_suite_and_marks_this_test`: open the tests drawer. Rows exist for every visible test, grouped (`tests-drawer:group:FakeLib`), with no checkbox (`_none(user, "tests-drawer:check:" + A)`). A's row has `dstf-row-current`. Typing `D` in `tests-drawer:needle` hides every row but D's.
  - `test_a_click_in_the_tests_drawer_opens_that_test_and_the_drawer_stays`: stage `LOOSEN` first. Click `tests-drawer:` + D. The page for D is built (`user.should_see(marker="model")` reads D) with the tests panel still visible, and `shell.session.spec.pending` still holds `LOOSEN`.
  Expected: markers missing on unchanged code. Show it.
- [ ] **GREEN** —
  - `TabState` gains `drawer: str | None = None` (`"tests"` / `"variables"` / None) and `tests_needle: str = ""`.
  - `shell/rail.py`:

```python
"""The Test page's rail (the owner's ruling, 2026-09-25; DESIGN.md "The
Variables drawer"): 40 px of labelled buttons down the left edge, and ONE
drawer at a time beside them, 320 px, which pushes the plots narrower
rather than covering them (`behavior=desktop`: below Quasar's 1024 px
breakpoint a drawer otherwise turns into an overlay). Plotly follows the
narrower slot by itself — NiceGUI's plotly element observes its size. Which
drawer is open is the tab's (`TabState.drawer`), so a switch of test keeps
it open."""

RAIL, PANEL = 40, 320


class Rail:
    def __init__(self, page: TestPage) -> None: ...
    def open(self, kind: str | None) -> None: ...  # None closes
    def toggle(self, kind: str) -> None: ...
    def refresh(self) -> None: ...  # the open drawer follows the Session
```

    `Rail.__init__` builds `ui.left_drawer(value=True).props(f"width={RAIL} behavior=desktop bordered")` with a no-wrap row: the rail column (two flat buttons, icon above an 11 px label: `format_list_bulleted` "Tests", marker `rail:tests`; `data_array` "Vars", marker `rail:variables`, tooltip "Variables") and one panel column per drawer (`drawer:tests`, `drawer:variables`), each `width: 320px`. `open(kind)` shows that panel, hides the other, sets the drawer's `width` prop to `RAIL + PANEL` (or `RAIL` for None), marks the open rail button `dstf-rail-on`, and records `state.drawer`. The page builds the rail in `build` after the header, and calls `rail.open(state.drawer)` once built.
  - `TestsDrawer` (in `rail.py`): a filter input (`tests-drawer:needle`, `debounce=250`, value `state.tests_needle`) over a `TestList(shell, on_open=…, current=model_id, marker="tests-drawer:")`, grouped and short always. `on_open` navigates like the Suite page's `_open` (`ui.navigate.to(f"/test/{quote(model_id, safe='')}")`). The drawer is built lazily the first time it opens, and repainted from the page's `handle` on the events the Suite page repaints on.
  - `style.py`: `.dstf-rail-on { background: var(--dstf-selection); color: var(--dstf-accent); }`, `.dstf-row-current { background: var(--dstf-selection); }`.
- [ ] **RED → GREEN (browser)** — `test_the_rail_narrows_the_plots_and_nothing_scrolls_sideways` in `tests/test_author_test_page_playwright.py`, over `_served`, at 1280×900 and again after `page.set_viewport_size({"width": 900, "height": 900})`: the first `.js-plotly-plot`'s width W0; click `rail:tests`; the width becomes W0 − 320 ± 4 within `EXPECT_TIMEOUT`; `document.documentElement.scrollWidth == window.innerWidth`; close; back to W0 ± 4. Screenshot at 900 with the drawer open → thumbnail → READ.
- [ ] **Mutations:** (a) no `behavior=desktop` (the 900 px width check fails: the drawer overlays); (b) `open` leaves the other panel visible (the one-at-a-time test fails).
- [ ] **Commit:** `test page: the rail and the Tests drawer`.

### Task 7 — The selected plot, Add series, Remove plot, Reset layout

**Files:** modify `src/dstf/authoring/shell/test_page.py`, `src/dstf/authoring/shell/plot_card.py`, `src/dstf/authoring/shell/tree_card.py`, `src/dstf/authoring/shell/style.py`; tests `tests/test_test_page.py`, `tests/test_author_test_page_playwright.py`.

- [ ] **RED (simulation)** — `class TestLayoutControls` over a `page_shell` fixture built on `_page_project` (seven plots: x, y, then five of x+y):
  - `test_the_first_plot_is_selected_and_select_moves_the_ring`: after the page builds, `page.selected == 0` and only card 0 has `dstf-plot-selected`. `page.select(3)` moves the class to card 3 only.
  - `test_add_series_selects_its_plot_and_opens_the_variables_drawer`: click `add-series:2` → `page.selected == 2`, the variables panel is visible, `state.drawer == "variables"`.
  - `test_remove_plot_stages_one_op_without_that_plot`: click `remove-plot:0` → `shell.session.spec.pending == [PatchOp(A, "replace", "/plots", <the seven plots minus the first>)]` (A's entry has a `plots` key). The page rebuilds with six cards.
  - `test_remove_plot_is_grey_when_it_strands_a_scored_variable`: over the default `shell` fixture (A: plots x and y, both scored), `remove-plot:0` is disabled and `remove-plot:0:why`'s tooltip text says `x is scored by 1 criterion and this is the only plot that shows it`. (The count comes from the leaves on x.)
  - `test_reset_layout_shows_while_the_entry_has_plots_and_stages_its_removal`: over `page_shell`, `tree:reset-layout` exists; clicking it stages `PatchOp(A, "remove", "/plots")`; after the sync the button is gone and the page draws one plot per tracked variable. Over the default `shell` fixture (no `plots` key), `_none(user, "tree:reset-layout")`.
  - `test_a_refused_layout_edit_is_said_and_changes_nothing`: make the Session refuse (`monkeypatch` `shell.session.stage` to raise `LockedError([A], "Rerun")`); click `remove-plot:1` → a notification with the lock's sentence; pending unchanged.
  Expected: markers and attributes missing on unchanged code. Show it.
- [ ] **GREEN** —
  - `TestPage` gains `self.selected: int = 0` and:
    - `select(index)`: clamp to the cards; set `selected`; `card.card.classes(add="dstf-plot-selected")` on that card and `remove=` on the others; `self.rail.refresh()`.
    - `scored() -> dict[str, int]`: `{variable: number of leaves}` over `leaves_of(self._view())`.
    - `stage_layout(layout, select=None) -> bool`: `op = layout_op(model_id, session.spec.entry(model_id), layout)`; when `select` is not None, set `self.selected = select` BEFORE staging (the rebuild the `spec` event causes reads it) and restore it if the stage raises; catch `LockedError`, `PatchError`, `ConfigError`, `JobRunningError` and `ui.notify(str(e), type="warning")`, returning False.
    - `reset_layout()`: `session.stage([reset_op(model_id)])` under the same catch.
    - `rebuild` ends by clamping `selected` to `len(self.cards) - 1` (0 when empty) and painting the ring (`select(self.selected)` without the rail refresh when there are no cards).
  - `PlotCard` toolbar, after Focus: `Add series` (`add-series:{index}`: `page.select(index)`, then `page.rail.open("variables")`) and `Remove plot` (`remove-plot:{index}`). Remove plot computes `after = remove_plot(layout, index)` and `stranded(layout, after, scored)`. When that is non-empty the button is disabled, and a wrapping span marked `remove-plot:{index}:why` carries a `ui.tooltip` with the sentence (a disabled Quasar button takes no pointer events, so the tooltip goes on its wrapper): `"{text} is scored by {n} criteri{on|a} and this is the only plot that shows it. Add it to another plot first, or remove its criteria."`. For several variables: the texts joined with ", ", and "are scored". The text is `page`'s label for the name (`card.texts`).
  - The card's own click selects it, outside the plot area only: `card.on("click", partial(page.select, index), js_handler="(e) => { if (!e.target.closest('.dstf-plot-slot')) emit(); }")`.
  - `TreeCard._line`, both branches (a test with no tree can have a layout): when `"plots" in (session.spec.entry(model_id) or {})`, end the line with `ui.button("Reset layout", on_click=page.reset_layout).props("flat dense no-caps").mark("tree:reset-layout")` and a tooltip "Stage removing this test's plots key: one plot per tracked variable".
  - `style.py`: `.dstf-plot-selected { box-shadow: inset 0 0 0 2px var(--dstf-accent); }`.
- [ ] **Existing tests that move:** the toolbar gains two buttons; a test that counts a card's buttons or reads the toolbar's marks in order is updated here, saying why.
- [ ] **RED → GREEN (browser)** — `test_a_click_selects_a_plot_outside_its_plot_area`: over `_served`, a real click on card 3's title moves `.dstf-plot-selected` to card 3; a real click inside card 4's `.js-plotly-plot` leaves it on card 3; a real click on a criteria row of card 1 moves it to card 1. Screenshot of the ring → thumbnail → READ.
- [ ] **Mutations:** (a) no `closest('.dstf-plot-slot')` guard (the plot-area click moves the ring); (b) Remove plot never disabled; (c) `stage_layout` not restoring `selected` on a refusal (add the assertion to the refused-edit test: `page.selected` unchanged).
- [ ] **Commit:** `test page: the selected plot, Add series, Remove plot, Reset layout`.

### Task 8 — The Variables drawer

**Files:** create `src/dstf/authoring/shell/variables_drawer.py`; modify `src/dstf/authoring/shell/rail.py`, `src/dstf/authoring/shell/style.py`, `tests/test_session.py` (fixture); tests `tests/test_test_page.py`, `tests/test_author_test_page_playwright.py`.

- [ ] **Fixture** — `_drawer_project(tmp_path)` in `tests/test_session.py`, beside `_plots_project`: `_two_var_project` with A's `result.json` carrying, after x and y, the untracked `a0`, `z`, `pipe.T[1]`, `pipe.T[2]`, `pipe.T[3]`, `port[2, 1].C[1]`, `port[2, 1].C[2]`, `port[2, 1].C[3]` and `CPUtime`, each a distinct ramp so a plot shows them apart (`[k * v for v in t]` for k = 3, 4, …). A's tree is the implicit one (x and y scored, one nrmse each).
- [ ] **RED (simulation)** — `class TestVariablesDrawer` over a shell on `_drawer_project`, the drawer opened with `rail:variables`:
  - `test_the_drawer_lists_the_groups_and_names_its_target`: group markers `var-group:Tracked`, `var-group:pipe`, `var-group:port`, `var-group:(top level)`, `var-group:Diagnostics` in that order; `var-target` reads `Checkboxes act on plot 1 of 2`; the pipe family's row `var-row:pipe.T[]` reads `pipe.T[1:3]`.
  - `test_a_checkbox_adds_a_series_to_the_selected_plot_as_one_op`: click `var-check:z` → `pending == [PatchOp(A, "add", "/plots", [{x, z}, {y}])]` (as raw dicts). Click `var-check:pipe.T[]` → still ONE op, now `add` of `[{x, z, pipe.T[1], pipe.T[2], pipe.T[3]}, {y}]`. The page rebuilt, with card 0's title naming the family.
  - `test_unchecking_removes_and_a_scored_last_plot_refuses`: after adding z, click `var-check:z` again → the op's value is back to `[{x}, {y}]`. `var-check:x` is disabled and `var-why:x` carries `x is scored by 1 criterion and this is the only plot that shows it. …`.
  - `test_new_plot_goes_below_the_selected_one_and_is_selected`: click `var-new:port[2, 1].C[]` → the op's value is `[{x}, {port[2, 1].C[1..3]}, {y}]`, `page.selected == 1`, and `var-target` reads `plot 2 of 3`.
  - `test_the_filter_globs_regexes_and_says_a_bad_regex`: type `pipe.T[?]` → only the pipe group; toggle `var-regex` and type `(` → `var-filter:why` reads `not a regular expression: …` and no rows; type `^port` → only port.
  - `test_a_family_opens_to_its_members`: click `var-open:pipe.T[]` → rows `var-row:pipe.T[1]` … `[3]` visible, each with its own check.
  - `test_the_drawer_follows_the_selected_plot`: `page.select(1)` → `var-target` reads `plot 2 of 2`, and `var-check:y` shows checked (aria-checked `true`).
  - `test_a_test_with_no_result_says_so`: open the drawer on B's page after deleting B's `result.json` from the work dir before `open` → `var-empty` reads `this test has no result`.
  - `test_the_cap_line`: a fixture variant whose `result.json` adds 250 untracked names `v0` … `v249` → `var-cap` reads `Showing 200 of {total} rows — narrow the filter`, where `{total}` is `cap`'s own total for that fixture, computed in the test from `variable_groups`, never hand-counted.
  Expected: missing markers on unchanged code. Show it.
- [ ] **GREEN** — `VariablesDrawer(page)` in `variables_drawer.py`, built into the rail's `drawer:variables` panel:
  - Head: "Variables"; a filter input `var-needle` (`debounce=250`, placeholder "filter: substring, or a glob with * ?"); two flat toggle buttons `var-regex` (`.*`) and `var-case` (`Aa`), each `aria-pressed`; a `var-filter:why` label for a bad regex.
  - `var-target`: `Checkboxes act on plot {selected+1} of {n}` with the ring swatch; a click scrolls the selected card into view (`scrollIntoView({block: "nearest"})`). With no plot: `No plot yet: + new plot adds one`.
  - Rows from `variable_groups(names=session.variable_names(m), tracked=session.tracked(m), diagnostics=config.diagnostic_variables, labels=session.labels(m), layout=session.layout(m), selected=page.selected if cards else None, scored=page.scored(), keep=matcher(...))`, then `cap(…, 200)`. With no names: `var-empty` reads `page.unscored_reason()`'s "this test has no result" when the result is missing, else "the result has no variables".
  - Each row (`var-row:{key}`, class `dstf-var-row`): a chevron `var-open:{key}` for a family; the check `var-check:{key}`, a flat round button with icon `check_box` / `indeterminate_check_box` / `check_box_outline_blank` for all / some / none of the members on the selected plot, `role="checkbox"`, `aria-checked` `true` / `mixed` / `false`. It is NOT a `ui.checkbox`: a `ui.checkbox`'s `set_value` fires its own `on_change`, and a repaint would stage an op. For a family, a ramp swatch of the family's hue (`HUES[0]`'s four stops). Then the text (mono), the muted name when it differs, and the trailing "plot 1, 3" (`elsewhere`, 1-based). Last, `var-new:{key}`, "+ new plot", visible on the row's hover only (CSS `.dstf-var-row .dstf-var-new { visibility: hidden } .dstf-var-row:hover .dstf-var-new { visibility: visible }`).
  - A stranded row: the check is disabled, and `var-why:{key}` (a span around it) carries the tooltip sentence of Task 7, over the stranded members.
  - Click on the check: all members on the plot → `remove_series(layout, selected, members)`, refused with the sentence if `stranded(...)` is non-empty (belt and braces: the row is already disabled); otherwise `add_series(layout, selected, members)`. Then `page.stage_layout(new)`.
  - Click on "+ new plot": `new_plot(layout, selected if cards else None, members)` → `page.stage_layout(new, select=at)`.
  - An opened family (`self.opened: set[str]`, kept across repaints of this page) draws its members' rows indented, each with its own check and "+ new plot", at most 50, then `… N more`.
  - The drawer is rebuilt when it opens, on `select`, and from the page's `sync` when it is open (a `spec` or `results` event for this model). It is not rebuilt while closed.
- [ ] **RED → GREEN (browser)** — `test_the_variables_drawer_edits_the_layout_with_a_real_mouse`, over a `_served` variant on `_drawer_project` (the helper takes the fixture as a parameter; the spec must still be byte-identical at the end, because the test ends with Discard):
  1. Real click on `add-series:1` → the ring on card 1, the drawer open, `var-target` "plot 2 of 2".
  2. Hover `var-row:z`: `var-new:z` becomes visible; move away: hidden again.
  3. Real click on `var-check:z` → card 1's legend gains `z`.
  4. Real click on `var-new:port[2, 1].C[]` → a new card 2, ringed, whose legend has ONE entry `port[2, 1].C[1:3]`. A real-mouse hover at mid-plot on card 2 reads `.hoverlayer text`, and one line contains `port[2, 1].C[2]` (a family member's hover names its index; the ", " must not break the template).
  5. The header reads `1 pending edit`. Discard → two cards, no ring moved off card 1.
  Screenshots after 3 and 4 → thumbnails → READ.
- [ ] **Mutations:** (a) the check is a `ui.checkbox` with `set_value` on repaint (a repaint stages an op: the follow test sees pending grow); (b) "+ new plot" not selecting the new plot; (c) the hover rule removed (step 2 fails).
- [ ] **Commit:** `test page: the Variables drawer edits the layout`.

### Task 9 — The header: a ⋮ menu, and Save names a new entry

**Files:** modify `src/dstf/authoring/shell/header.py`; tests `tests/test_shell.py`, `tests/test_author_suite_playwright.py`, `tests/test_author_test_page_playwright.py`.

- [ ] **RED** —
  - `test_save_caption_counts_new_entries` (pure, `tests/test_shell.py`):

```python
from dstf.authoring.shell.header import save_caption


def test_save_caption_counts_new_entries():
    loaded = {"tests": [{"model": "M"}]}
    edit = PatchOp("M", "replace", "/plots", [])
    new = PatchOp("N", "add", "/plots", [])
    assert save_caption([], loaded, "test_spec.json") == (
        "Save 0 spec edits (0 tests) to test_spec.json"
    )
    assert save_caption([edit], loaded, "test_spec.json") == (
        "Save 1 spec edit (1 test) to test_spec.json"
    )
    assert save_caption([edit, new], loaded, "test_spec.json") == (
        "Save 2 spec edits (2 tests, 1 new entry) to test_spec.json"
    )
```

  - `test_the_header_menu_holds_the_rarely_used_actions` (simulation, `tests/test_shell.py`): the header row has `save`, `discard` and `header-menu`; `export`, `other-backend`, `dashboard` and `stop` are inside the menu element (their ancestors include the `ui.menu` under `header-menu`). With nothing pending, the `export` item is disabled and `export:why` reads `no pending edits`.
  - Browser (`tests/test_author_suite_playwright.py`), `test_the_header_fits_900_px`: at 900×900 on the Suite page and on a Test page, `scrollWidth == innerWidth`, and the right edge of `save`, `discard` and `header-menu` is ≤ `innerWidth`. Screenshot → thumbnail → READ.
  Expected: ImportError / missing markers on unchanged code. Show it.
- [ ] **GREEN** —

```python
def save_caption(pending: Sequence[PatchOp], loaded: dict, file_name: str) -> str:
    """The Save button's caption (brief §3.3: object, effect, count). A
    pending edit to a model with no entry in the file as loaded CREATES
    that entry on Save, and the caption says so (the owner's ruling,
    2026-09-25): a layout staged for a test tracked only by its `.mo`
    annotation writes that test's first entry."""
    from ...discovery.spec_update import spec_entry

    models = list(dict.fromkeys(op.model_id for op in pending))
    new = sum(1 for m in models if spec_entry(loaded, m) is None)
    tests = plural(len(models), "test")
    what = f"{tests}, {plural(new, 'new entry', 'new entries')}" if new else tests
    return f"Save {plural(len(pending), 'spec edit')} ({what}) to {file_name}"
```

  `plural` (`authoring/capabilities.py`) takes regular plurals only today (`f"{count} {noun}s"`), and "entry" is not one. It gains an optional third parameter, `many: str | None = None`, used for a count other than 1, and its docstring's "Regular plurals only" sentence says so. A test for it goes beside its existing ones (`grep -rn "plural(" tests/`). `Header.refresh` sets `self.save` from `save_caption(s.spec.pending, s.spec.loaded, s.spec.path.name)`.
  - The row: after Save and Discard, a flat round `more_vert` button marked `header-menu` holding a `ui.menu()` with four `ui.menu_item`s: Export (`export`; its caption as today; disabled with its reason shown as a second line `export:why` when nothing is pending), Open in another backend… (`other-backend`), dashboard (`dashboard`; opens `/dashboard/dashboard.html` in a new tab), Stop server (`stop`; negative colour). The menu items keep the handlers they have today. The module docstring's first paragraph says the menu and why (the 900 px header cut off "dashboard" and "Stop server"; the owner chose one layout at every width).
- [ ] **Existing tests that move:** those that click `export`, `stop`, `other-backend` or the dashboard link: `grep -n '"export"\|"stop"\|"other-backend"\|dashboard' tests/test_shell.py tests/test_author_suite_playwright.py tests/test_author_test_page_playwright.py`. In the browser, open `header-menu` first. In the simulation, if the user simulation finds a closed menu's items, leave those tests alone; if it refuses them, click `header-menu` first. Say which in the commit message.
- [ ] **Mutations:** (a) `new` counted over the EFFECTIVE document instead of `loaded` (never counts: the pending op already made the entry); (b) Export left in the row (the 900 px test fails at 900).
- [ ] **Commit:** `header: a menu for the rarely used actions; Save names a new entry`.

### Task 10 — Docs, D134, the roadmap, ideas

**Files:** `DESIGN.md`, `docs/decisions.md`, `CLAUDE.md`, `docs/superpowers/plans/2026-09-15-authoring-tool-redesign-roadmap.md`, `docs/ideas.md`.

- [ ] `DESIGN.md`:
  - "The Variables drawer" gains an *As built* paragraph: the selected plot and how it is chosen, the target line, the drawer as the one place for layout edits, the stranded rule, the anchored glob, the 200-row cap and the 50-member family, the tab-remembered drawer.
  - "Plots are modular"'s *As built*: "Series chips, … and Remove plot are scheduled" becomes what was built: Add series and Remove plot in the toolbar, no series chips (the owner's ruling), Reset layout in the tree card's line.
  - "Header": the ⋮ menu and its four items; Save's caption counts a new entry.
  - "Array families on a plot": the rule sentence gains the comma case.
- [ ] `docs/decisions.md`: **D134 — The layout is edited on the plot you selected, one op on `/plots`; the rail holds two drawers (2026-09-25).** Context (3c's rulings), Decision (the five architecture moves), Consequences (the collapse rules and the one they refuse; the family rule's change and the differential that pins it; the folded criteria row moved to 3d; what the gate measured).
- [ ] `CLAUDE.md`: the Project Overview's history list gains "D134 the rail and drawers". The `dstf author` comment block in "Running the Tool" gains one sentence: the rail opens the Tests drawer (switch test) and the Variables drawer (add a series to the selected plot, or a new plot, and Remove plot / Reset layout to take them away).
- [ ] The roadmap: after "3c scoped (2026-09-25)", a "3c BUILT" paragraph with the commits and the gate's numbers (from Task 11).
- [ ] `docs/ideas.md`: the build's residuals, as one dated entry.
- [ ] **Commit:** `docs: 3c — the rail and drawers, D134`.

### Task 11 — Gate over a COPY of TRANSFORM (orchestrator)

- [ ] Copy `TRANSFORM-UnitTests/ReferenceResults` and the last work dir to the scratchpad; sha256 every file of the owner's two trees before and after — identical.
- [ ] **The family rule, counted.** Over every test of the copy through `Session`: for each test, the result's names and every label (`Session.variable_names`, `Session.labels`). Count families (groups of 2+) under the rule BEFORE (a `git archive` of the commit preceding Task 1, absolute `PYTHONPATH`, `dstf.__file__` asserted) and AFTER (HEAD). List every name or label whose `family_of` changed. Expected: only the comma-before-last-index shape changes `family_of`. Task 1's review parsed all 332 TRANSFORM `.mo` files read-only: 6 expressions change (`pipe_single` / `pipe_nParallel.massPorts[4, 1].C[1]` in 3 models), but groups of 2+ stay 10 before and 10 after, because each is a family of ONE (one species per port). So "no new fold in TRANSFORM" is the expected result, not a failed gate. Result-file names are counted as well and may differ.
- [ ] **The drawer over real data.** For the test whose result lists the most names: time `variable_names` + `variable_groups` + `cap`; the number of groups, folded rows and total rows; the time to open the drawer in Chromium (click to `var-target` visible).
- [ ] **What the feature did, in Chromium, over the copy** (a test with an array in its result): open the Test page; open the Variables drawer; filter with a glob that selects one array; "+ new plot" on the folded row → a new card, ringed, legend one entry; check a second variable onto it; Remove plot on another card that strands nothing; the header reads `1 pending edit` and Save's caption; **Save** (the COPY's spec only) → the copy's entry has the `plots` key `parse_plots` accepts; `dstf compare --report` over the copy → the report page for that test draws the same plots, and the verdicts are the same as before (330 pass, 1 known error). Then Reset layout → Save → the key is gone. Screenshots → thumbnails → READ, one of them at 900 px with the drawer open and nothing cut off in the header.
- [ ] Hand the owner: the counts, the timings and the pictures.
