# The Test Page (step T, with S2b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The authoring tool's Test page gets the owner's four rulings: a status strip under the test's row, in the sticky part, that says the test's situation and fills the one next action; one way to add a criterion — every `+` opens one inline row where it was clicked, a new criterion lands right after the clicked one, a combinator's kind is an inline dropdown; a pending-edits drawer that previews each test's entry as `test_spec.json` will hold it (a `difflib` diff); and one control set on the Tests and Variables drawers (filter with `.*` / `Aa`, sort, group, names, and on the Tests drawer compact status pills with EDITED).

**Architecture:** Session-tier pure modules decide what the page shows: `authoring/situation.py` (a test's situation and its strip sentence), `authoring/spec_preview.py` (the entry diff and the variable spans in it), and new parameters on `variable_rows.variable_groups` (group, order). A capability declares, per situation, whether it is the next action or goes into the ⋮ menu (`Capability.emphasis`, read with `getattr` like `icon`) — the shell still names no capability. The VARIABLE placement becomes inline (an `InlineHost` in the plot's new criteria header), and `Panel` gains `lead()` so the tree's combinator row draws its kind dropdown where the kind is read.

**Tech Stack:** Python 3.10+, NiceGUI 3.16 (`[author]`), Quasar, `difflib` (stdlib), pytest + pytest-asyncio (NiceGUI user simulation), Playwright + Chromium, uv.

**Owner rulings this plan implements (2026-09-26, board https://claude.ai/artifact/88WhEgGHtSjT3pkiJTJ4Sm v2):**
- **Q2 A** — a full-width status strip directly under the test's row, inside the part that stays at the top. It says the test's situation and colours the one next action. Accept and Rerun leave the test's row. Five states drawn or named: passes, no edits (grey; Rerun quiet; Accept moves into the strip's ⋮: "there is nothing new to accept"); fails (red; "Fails 1 of 3 criteria. If the new behaviour is right, accept it as the baseline."; Accept filled, Rerun quiet); edits pending (amber; "2 unsaved edits · verdict would change PASS → FAIL"; Preview and save filled, Discard quiet); no baseline ("No baseline yet — accept this result as the first one", Accept filled); edits to the simulation settings ("Your edits change the simulation settings — rerun to see them", Rerun filled). Reverses DESIGN.md "Buttons" (Accept and Rerun always outlined).
- **Q3 A** — every `+` opens the same inline row where it was clicked, no dialog. The plot's "Add a criterion for x" button becomes a small `+` in its criteria header. A `+` on a criterion adds the new one right AFTER it; on a combinator, as its LAST child. After Add the new criterion's editor is open, seeded from the data. A combinator shows its kind as a dropdown changed in place (and / or / k-of-n / warn / weighted). Reverses D131 (Add a criterion in a dialog) and D133 (a combinator's kind in its Edit form).
- **Q6 A** — the pending-edits drawer is the preview: per test, the entry before and after, added lines green, removed lines red; variables read by their label with the result-file name muted beside it; Save stays one click in the header; the drawer has its own "Save these". Built on `difflib` over the entry as `json.dumps(indent=2)` writes it.
- **Q7** — one control set on every list: the Tests drawer gains compact status chips (with EDITED) and sort, group and names controls; the Variables drawer gains sort, group (by component / flat / tracked first) and names (expression / result-file name / both) menus. View controls are icons with tooltips.
- Deferred here by the bug batch (D135 "Not built here"): the "Add a criterion for x" dialog not opening the new editor; the tree `+` wording and placement; the weighted combinator's threshold hint.

**Planner's choices (said out loud; cheap to change on the owner's word):**
1. Unwrap moves from the combinator's Edit form into the kind dropdown, below a separator ("unwrap — its children take its place"). The Edit (tune) icon stays only on k-of-n and weighted nodes, whose form holds their fields (k; weights, threshold, direction) — and/or/warn have none. Choosing k-of-n or weighted in the dropdown opens that form preset to the new kind (it needs k / a threshold before it can be staged); and / or / warn stage at once.
2. The strip's Discard discards THIS test's pending edits (`Session.discard(models=...)`); the header's Discard still discards all.
3. The Tests drawer's sort menu: package order (today's) / name A→Z / status, failures first. The Variables drawer's sort menu: result-file order (today's) / A→Z by the text shown.
4. The drawer's per-test summary lines drawn on the board ("edit nrmse on pulse.y: tolerance 0.01 → 0.005") are NOT built: the ruling's text names the diff, and a generic op→sentence renderer is its own piece of work. Each test's section has its edit count and verdict line instead. Logged in `docs/ideas.md`.
5. "Simulation settings" means an op whose path is `/simulation` or under it.

**Evidence (read at 14f1ba7):**
- The sub-header is a second row inside Quasar's fixed `ui.header` (`header.py:205-212`, `style.py:38-63`, D131 decision 8): there is no CSS sticky; the strip is a third row there. `TestPage._subheader` (`test_page.py:152-171`) holds `← Suite`, the model, the badge, `meta` and `ActionBar(shell, Placement.TEST, ...)`. `_paint` (`:605-626`) repaints on every event.
- `Session.stage` (`session.py:846-912`) rescores into `session.comparisons`: the shown verdict IS the edited spec's. The saved spec's verdict is not kept; `SpecDocument.loaded` (`spec_document.py:50`) is the document as read. `Session.discard()` drops every pending op (`:914-922`). `Session.save()` writes `json.dumps(data, indent=2) + "\n"` through `apply_validated_patches` (`discovery/spec_update.py:183-207`).
- `ActionBar` (`actions.py:120-269`) rebuilds its buttons only when the set of capability names changes. Accept (`builtin/accept.py:93-159`) and Rerun (`builtin/rerun.py:38-121`) are at SUITE and TEST and declare `icon`. `tests/test_capability_conformance.py::test_the_shell_names_no_capability` forbids a capability's name in the shell.
- `EditTree` (`builtin/edit_tree.py`) is the only capability at NODE and VARIABLE. `act("add")` appends at `{under}/children/-`; beside a root leaf it replaces `/metrics` with `and[node, leaf]` (`:217-236`). At VARIABLE its panel is `_add_form(..., under=ROOT)` with no `close` and no `added` (`:194-205`), and `ActionBar.open` builds `ShellPanel` with no `reveal` (`actions.py:263`): the two causes of "Add a criterion for x does not open the new editor". The footer path has no `_NO_ADD` gate (`:94-98`).
- The kind is edited only in `_combinator_fields` (`:290-378`) — a `ui.select(label="Combinator")`, the chosen kind's fields, Unwrap / Cancel / Apply. The weighted threshold is a bare `ui.number(label="Threshold")` with no hint (`CombinatorSpec.threshold`, `comparison/tree_spec.py:78-93`; pass is `Σ wᵢ·scoreᵢ < threshold` for `less`, `> threshold` for `greater`, `metric_tree.py:164-216`).
- The tree card's row (`tree_card.py:263-293`): pill, `node_text` label, readout (leaves), `ui.space()`, `InlineHost(Placement.NODE, ..., row=RowTarget(...))`, then the slot column. A plot card's footer (`plot_card.py:425-447`) is one `ActionBar(Placement.VARIABLE, ...)` per tracked variable.
- `PendingDrawer` (`header.py:417-444`): a `ui.right_drawer`; one raw line per op (`drawer-op` markers).
- `TestList` (`test_list.py`): substring filter on the model id, grouped by package, short names; `TabState` (`tabstate.py:18-23`) has `checked`, `drawer`, `tests_needle`. The Variables drawer's needle / regex / case live on the drawer instance. `variable_rows.matcher` (`:88-135`) is the budgeted regex / glob / substring matcher. Within a group, rows are in first-appearance (result-file) order (`figures/families.py:146-163`).

---

## Rules for every task (the project's build loop)

- Another agent may be alive in the repo: commit ONLY with `git commit -F <your own msg file> -- <explicit paths>` (a message file named for your task, e.g. `<scratchpad>/msg-t4.txt`); never `--amend`, never `git stash` (not even inside a one-liner), never `git checkout -- <path>` (copy aside and restore bytes). Never push. No record id (the `.soul/` ids) in any tracked file.
- Every shell command in the FOREGROUND with `cd /mnt/d/Modelica/DynamicSystemsTestingFramework &&` in the same line; assert path variables non-empty before `cp`/`rm`; kill only processes you start. `export PYTHONDONTWRITEBYTECODE=1` when applying a mutant.
- Test-first: write the test, run it, SEE it fail for the stated reason, then implement. Locate code by content, not by the line numbers quoted here (they drift).
- A UI change is verified in a real browser and by READING a screenshot thumbnail: a throwaway Playwright script over a temp FakeLib project, a viewport screenshot at 1280 and at 900 px wide, `python3 ~/.claude/skills/ue-windows-wsl/scripts/render_check.py thumb <img> -o <out>.thumb.jpg`, READ it, describe it in the report. Never a full-res image; never `main([..., "author"])` in a scratch script against a real work dir.
- A design choice not written in this plan is not made by the implementer: stop and report.
- Gates before each commit: the task's tests, `uv run --no-sync ruff check`, `uv run --no-sync ruff format --check`; before the last commit of a task also bare `uv run --no-sync mypy`, `uv run --no-sync pytest -m "not playwright" -q`, and the Playwright files the task touched.

## File map

| File | Change |
|---|---|
| `src/dstf/authoring/situation.py` (new) | T1: `Situation`, `Strip`, `strip_for`. |
| `src/dstf/authoring/spec_document.py`, `session.py` | T1: `saved_entry`, `touches_simulation`, `Session.discard(models=)`, `Session.saved_comparison`. |
| `src/dstf/authoring/capabilities.py`, `builtin/accept.py`, `builtin/rerun.py`, `shell/actions.py` | T2: `Emphasis`, `emphasis` maps, `ActionBar(emphasis=)`. |
| `src/dstf/authoring/shell/strip.py` (new), `test_page.py`, `header.py`, `style.py` | T3: the strip. |
| `src/dstf/authoring/builtin/edit_tree.py` | T4-T6: add-after, kind dropdown, the plot's `+`. |
| `src/dstf/authoring/capabilities.py`, `shell/actions.py`, `shell/tree_card.py`, `shell/plot_card.py` | T5-T6: `Panel.lead`, VARIABLE inline. |
| `src/dstf/authoring/spec_preview.py` (new), `shell/header.py` | T7-T8: the preview drawer. |
| `src/dstf/authoring/needle.py` (new), `variable_rows.py`, `shell/tabstate.py` | T9: the matcher moves (refactor commit); drawer state per tab. |
| `src/dstf/authoring/shell/test_list.py`, `rail.py`, `drawer_controls.py` (new) | T10: the Tests drawer's control set. |
| `src/dstf/authoring/variable_rows.py`, `shell/variables_drawer.py` | T11: the Variables drawer's control set. |
| `docs/decisions.md`, `docs/usage.md`, `docs/ideas.md`, `CLAUDE.md`, the roadmap | T12. |

---

## Part 1 — the status strip

### Task 1: A test's situation, its saved verdict, a per-test discard

**Files:** Create `src/dstf/authoring/situation.py`; Modify `src/dstf/authoring/spec_document.py`, `src/dstf/authoring/session.py`; Tests `tests/test_situation.py` (new), `tests/test_session.py`, `tests/test_spec_document.py`.

- [ ] **Step 1: Failing tests.**
  - `tests/test_spec_document.py`: `SpecDocument.saved_entry(model_id)` returns the entry of `loaded` (a deep copy; mutating it leaves `loaded` alone) and `None` for a model the loaded document does not name — whatever is pending. `touches_simulation(PatchOp("A", "replace", "/simulation/stop_time", 5))` is True, `"/simulation"` True, `"/simulationx"` False, `"/metrics/children/0"` False.
  - `tests/test_session.py` over `_project` (A FAIL, B NO_REF, C SIM_FAIL, D PASS): stage an op on A and one on D; `session.discard(models={"A"})` leaves only D's op pending, A's comparison back to FAIL, D's still the edited one; `session.discard()` drops all (today's behaviour). `session.saved_comparison("A")` with nothing pending IS `session.comparisons["A"]`; after staging a tolerance loosen on A that makes it PASS, `classify(session.comparisons["A"]) == "PASS"` and `classify(session.saved_comparison("A")) == "FAIL"`, and `session.comparisons` / `project.tests` are unchanged by the call. A model whose entry is NEW in the pending ops and not scanned: `saved_comparison` is None.
  - `tests/test_situation.py` — `strip_for(...)` is pure; its keyword inputs: `running: str | None` (the running job's label when it locks this test), `edits: int`, `sim_edits: bool`, `status: str | None` (the classifier's word of the current comparison, None while unscored), `saved_status: str | None`, `has_result: bool`, `scoring_failed: bool`, `fails: int`, `criteria: int`, `advisory: int`. Assert each row (situation, tone, sentence):

    | inputs | situation | tone | sentence |
    |---|---|---|---|
    | running="Rerun", status="FAIL" | RUNNING | grey | `Rerun is running on this test.` |
    | edits=2, sim_edits=True | SIM_EDITS | amber | `2 unsaved edits change the simulation settings — rerun to see them.` |
    | edits=2, status="FAIL", saved_status="PASS" | EDITS | amber | `2 unsaved edits · verdict would change PASS → FAIL` |
    | edits=1, status="PASS", saved_status="PASS" | EDITS | amber | `1 unsaved edit · verdict stays PASS` |
    | edits=1, status="PASS", saved_status=None | EDITS | amber | `1 unsaved edit · a new entry, scored PASS` |
    | scoring_failed=True | SCORING_FAILED | red | `Scoring failed; the tree card says why.` |
    | status=None, has_result=True | WAITING | grey | `Scoring…` |
    | status=None, has_result=False / status="SIM_FAIL" with no result | NO_RESULT | grey | `No result yet — rerun to simulate it.` |
    | status="SIM_FAIL", has_result=True | SIM_FAIL | red | `The simulation failed. Fix the model or its settings, then rerun.` |
    | status="NO_REF" | NO_REF | amber | `No baseline yet — accept this result as the first one.` |
    | status="FAIL", fails=1, criteria=3 | FAIL | red | `Fails 1 of 3 criteria. If the new behaviour is right, accept it as the baseline.` |
    | status="FAIL", fails=0 | FAIL | red | `Fails against its baseline. If the new behaviour is right, accept it as the baseline.` |
    | status="PASS" | PASS | grey | `Passes against its baseline.` |
    | status="PASS", advisory=1 | PASS | grey | `Passes against its baseline, with 1 advisory failure.` |

    Precedence is the table's order (RUNNING first). `Situation` is an `Enum` whose values are the lower-case names; `Strip` a frozen dataclass `(situation, tone, sentence)`. Use `capabilities.plural` for counts.
  - Run: FAIL (missing names).
- [ ] **Step 2: Implement.**
  - `SpecDocument.saved_entry` finds the entry in `loaded["tests"]` by `model`, deep-copied. `touches_simulation(op)` lives beside `PatchOp`.
  - `Session.discard(self, models: set[str] | None = None)`: `None` = all (today). Otherwise keep the pending ops of other models, in order, and `_rebuild_tests(models)`. Refused while a job runs, as today.
  - `Session.saved_comparison(model_id) -> TestComparison | None`: with no pending op for the model, return `self.comparisons.get(model_id)`; otherwise merge `project.scanned` against `self.spec.loaded` (`self._merge`), take that model's `TestModel` (None if absent) and score it with `compare_one` exactly as `_score` does — WITHOUT writing `self.comparisons` or `project.tests`. No cache (the strip asks for one test, the drawer for the tests with edits); the gate measures it over TRANSFORM.
  - `situation.py`: `Situation`, `Strip`, `strip_for` per the table. Pure: no Session or UI import.
  - Run Step 1: PASS. Mutants to run and report: `discard(models=)` dropping every op; `saved_comparison` returning `self.comparisons[m]` unconditionally; EDITS checked before SIM_EDITS.
- [ ] **Step 3: Commit** "session: a test's saved verdict, a per-test discard, and the strip's situation".

### Task 2: A capability says when it is the next action

**Files:** Modify `src/dstf/authoring/capabilities.py`, `builtin/accept.py`, `builtin/rerun.py`, `shell/actions.py`; Tests `tests/test_action_icons.py`, `tests/test_builtin_capabilities.py`, `tests/test_capability_conformance.py`.

- [ ] **Step 1: Failing tests.**
  - `capabilities.Emphasis` is an Enum with `NEXT` and `MENU`. `Accept.emphasis == {FAIL: NEXT, NO_REF: NEXT, PASS: MENU, EDITS: MENU, SIM_EDITS: MENU, SIM_FAIL: MENU, NO_RESULT: MENU}` and `Rerun.emphasis == {SIM_FAIL: NEXT, NO_RESULT: NEXT, SIM_EDITS: NEXT, EDITS: MENU}` (keys are `Situation` members).
  - `ActionBar(..., emphasis=lambda cap: ...)` over the host harness (`tests/capability_host.py`) with two fake capabilities: one answering NEXT draws a filled button (`unelevated` prop, primary colour) marked `action:<name>`; one answering None draws a quiet flat button; one answering MENU is not a button in the bar but an item (`role=menuitem`, marker `menu-action:<name>`) of a ⋮ icon button (`action-more`, tooltip "More actions") at the bar's end, whose click opens the SAME dialog `ActionBar.open(cap)` opens, and whose disabled state carries the `available` reason as its tooltip; with no MENU capability there is no ⋮. A bar whose emphasis answer changes between two `refresh()` calls rebuilds (the filled button becomes quiet, the MENU one becomes a button). An `ActionBar` built without `emphasis` draws exactly today's outlined buttons (the Suite's icon bar unchanged).
  - The conformance scan still passes (the bar reads `getattr(cap, "emphasis", {})` through the callable the page passes; no name).
  - Run: FAIL.
- [ ] **Step 2: Implement.** In `capabilities.py` document `emphasis` in the `Capability` docstring beside `icon` (optional, read with `getattr`; "a mapping from a `Situation` to `Emphasis.NEXT` — drawn filled — or `Emphasis.MENU` — drawn in the bar's ⋮ menu; a situation it does not name draws it quiet"). `ActionBar` keeps a `(name, emphasis)` list as its rebuild key. Run: PASS. Mutants: the rebuild key back to names only; MENU drawn as a button.
- [ ] **Step 3: Commit** "actions: a capability declares when it is the next action; a bar fills it, or tucks it into its menu".

### Task 3: The strip on the Test page

**Files:** Create `src/dstf/authoring/shell/strip.py`; Modify `shell/test_page.py`, `shell/header.py`, `shell/style.py`; Tests `tests/test_test_page.py`, `tests/test_author_test_page_playwright.py`.

**The strip** (`StatusStrip(page)`): a third row inside the header, directly below the sub-header, full width, `dstf-strip dstf-strip-<tone>` (marker `strip`), a 3px left border and a tinted background from the theme's tokens (grey: `--dstf-pending-*` or the neutral token the theme already has for "queued"; red: `--dstf-fail-*`; amber: `--dstf-warn-*`) — no raw hex. Left: the sentence (`strip-text`). Right, in order: the shell's own buttons for EDITS and SIM_EDITS — **Preview and save** (`strip-preview`; filled in EDITS, quiet in SIM_EDITS; opens the pending drawer scrolled to this test, Task 8 makes the drawer the preview) and **Discard** (`strip-discard`, quiet; discards THIS test's edits — `session.discard(models={model_id})`, the header's refusal while a job runs shown the same way); then the TEST-placement `ActionBar` with `emphasis=lambda cap: getattr(cap, "emphasis", {}).get(strip.situation)`. The sub-header keeps `← Suite`, the model, the badge and `meta`; its `ActionBar` moves into the strip. `TestPage._paint` computes the strip's inputs (the classifier over `session.comparisons[m]`, `saved_comparison` only when the test has pending ops, `spec.pending` for `edits` / `touches_simulation`, `session.running` + `is_locked`, the model's result, the shell's failed-scoring set, the leaf counts from `session.tree_view(m)` through `tree_text.leaves_of` + `leaf_status`) and calls `strip.paint(strip_for(...))`. The header's height grows by the strip; content below it is not covered (the page's top padding follows the header — check at 900 px, where the sub-header wraps).

- [ ] **Step 1: Failing tests.** User simulation over `_project`: A's page shows `strip` with the FAIL sentence, Accept filled and Rerun quiet; D's page the PASS sentence, Rerun quiet, no Accept button and a ⋮ whose menu holds Accept; B's page (NO_REF) Accept filled; staging an edit on D turns the strip amber with `1 unsaved edit · verdict stays PASS` (or `would change`, per the edit), Preview and save filled, Accept and Rerun in the ⋮; the strip's Discard leaves another test's staged op pending; a staged `/simulation/stop_time` op fills Rerun. The sub-header has no `test-actions` bar. Playwright: the strip stays at the top while the page scrolls (the existing sticky test extended), its left edge and right edge inside the viewport at 1280 and 900 px, and the first plot card's top is below the strip's bottom.
- [ ] **Step 2: Implement.** LOOK at A (fail), D (pass) and D with an edit, at 1280 and 900. Run: PASS. Mutants: the page asking `saved_comparison` for every test on every event (a test counts calls: once per paint, only with edits); the strip's Discard calling `discard()`.
- [ ] **Step 3: Commit** "test page: a status strip under the test's row fills the one next action".

---

## Part 2 — one way to add a criterion

### Task 4: `+` adds right after the clicked criterion

**Files:** Modify `src/dstf/authoring/builtin/edit_tree.py`; Tests `tests/test_builtin_capabilities.py`, `tests/test_test_page.py`.

- [ ] **Step 1: Failing tests.** `EditTree.act(action="add", path=P, ...)`:
  - P a combinator → `add P/children/-` (unchanged).
  - P = `/metrics/children/1` (a leaf under the root `and` of three) → `add /metrics/children/2` — the new leaf right after it.
  - P = `/metrics/children/0/children/0` → `add /metrics/children/0/children/1`.
  - P = `/metrics` a root leaf → `add /metrics {"combinator":"and","children":[node, leaf]}` (unchanged).
  - `_added_path` predicts each (the path `reveal` opens): `P/children/<len>`, `/metrics/children/2`, `/metrics/children/0/children/1`, `/metrics/children/1`.
  - The `+` tooltip reads `Add a criterion after this one` on a leaf and `Add a criterion at the end of this <kind>` on a combinator; the form row's title `Add a criterion after <leaf text>` / `Add a criterion at the end of <node text>`; `_ADD_SENTENCE` becomes `Values come from the data where the metric needs them.` (the title now says where). Page test: on `_page_project`'s A (`and[tube·x, range·x, range·y]`), `+` on the tube row then Add stages `add /metrics/children/1`, and the new criterion's editor is open (its `dstf-editor` under the new row).
  - Run: FAIL.
- [ ] **Step 2: Implement.** Mutants: insert at `index` (before, not after); `_added_path` still predicting `/-`.
- [ ] **Step 3: Commit** "edit tree: a criterion's + adds the new one right after it".

### Task 5: A combinator's kind is a dropdown in its row

**Files:** Modify `src/dstf/authoring/capabilities.py` (`Panel.lead`), `shell/actions.py` (`ShellPanel.lead`, `RowTarget.lead`), `shell/tree_card.py`, `builtin/edit_tree.py`, `tree_text.py` (a count-only text); Tests `tests/test_builtin_capabilities.py`, `tests/test_test_page.py`, `tests/test_author_test_page_playwright.py`.

**`Panel.lead(build)`**: "draw `build()` in the node's lead place — beside its status, where its kind is read. A placement without one raises (like `row`)." `RowTarget` gains `lead: ui.element | None`; the tree card's row creates a `ui.element("span")` right after the pill and sets `target.lead` before `host.build()`; `ShellPanel(lead=...)` draws into it. After the host builds, a row whose lead holds content shows the COUNT text (`tree_text.count_text(node)`: `3 criteria`, `k=2 · 3 criteria`, `threshold 0.5 · 3 criteria` — `node_text` without the kind) instead of `node_text`; the row's painter keeps that choice on repaint. A host that cannot draw (EditTree unavailable while a job runs) leaves the lead empty and the row reads `node_text` — the kind never disappears.

**The dropdown** (EditTree's NODE cluster, on combinator nodes only, via `ui_panel.lead`): a dense borderless `ui.select` (marker `kind:<path>`, `aria-label` "Combinator kind") with `and`, `or`, `k-of-n`, `warn`, `weighted`, a separator, and `unwrap — its children take its place`. Choosing:
- `and` / `or` → `act("edit", kind=...)` at once (removes k / weights / threshold / direction, as `edit` does today).
- `warn` → at once; a node with more than one child is refused by the validator and the reason shows in the row (as any refused act does today); the select returns to the node's kind on repaint.
- `k-of-n` / `weighted` → opens the node's Edit form row preset to that kind (nothing staged until Apply).
- `unwrap…` → `act("unwrap")` (today's semantics, its refusals shown in the row).

**The Edit (tune) icon** stays only on `k-of-n` and `weighted` nodes; its form has no kind select (the kind comes from the node, or from the dropdown's preset), just that kind's fields, the sentence, Cancel and Apply (Unwrap has moved). The weighted form's Threshold gets a hint under it that follows the Direction select: `Passes when Σ weight × score over the children is below the threshold (lower is better).` / `… above the threshold (higher is better).`

- [ ] **Step 1: Failing tests.** User simulation on `_page_project` A: the root row's lead holds `kind:/metrics` reading `and`, the row's text reads `3 criteria`; choosing `or` stages `replace /metrics/combinator "or"` and nothing else; choosing `k-of-n` opens the form row titled `Edit this combinator` with a `k` field and stages nothing until Apply; `warn` on the 3-child root shows the validator's reason in the row and stages nothing; `unwrap…` on a one-child `and` stages the child in its place; an `and` node has no `edit:<path>` icon, a `k-of-n` node has one and its form has no `Combinator` select; the weighted hint text follows Direction. With a job locking the test the root row reads `and · 3 criteria`. Playwright: the dropdown opens with a real mouse and its menu lists the six entries; LOOK at 1280.
- [ ] **Step 2: Implement.** Mutants: the count text shown when the lead is empty; `and`→`or` also re-adding k; k-of-n staging at once without k.
- [ ] **Step 3: Commit** "edit tree: a combinator's kind is a dropdown in its row; unwrap moves into it".

### Task 6: The plot's `+`, inline; the editor opens after Add

**Files:** Modify `src/dstf/authoring/capabilities.py` (the `Placement` docstring: VARIABLE is inline), `builtin/edit_tree.py`, `shell/plot_card.py`, `shell/actions.py` (docstrings: `ActionBar` hosts SUITE and TEST); Tests `tests/test_test_page.py`, `tests/test_builtin_capabilities.py`, `tests/test_author_test_page_playwright.py`.

A plot card with tracked variables gets a criteria header row above its criteria (`criteria-header:<index>`): a muted `Criteria` label and, at its right, ONE `InlineHost(Placement.VARIABLE, Selection(tests={m}, variable=<the plot's first tracked variable>, texts=scope), row=RowTarget(page, f"plot:{index}", header, ROOT))` with the slot below the header. The footer `ActionBar`s go. At VARIABLE, `EditTree.panel` draws one small round `+` (`add-plot:<index>`, tooltip `Add a criterion for <text>`) that opens the form row (`ui_panel.row`) titled `Add a criterion for <text>` — the variable select preset to that variable and offering the test's variables as today — with `close` and `added` wired so a successful Add reveals the new criterion's editor. It lands at the end of the root combinator (`/metrics/children/-`) or, under a root leaf, as `and[leaf, new]`. The `+` is grey with `_NO_ADD`'s reason when the root is `warn` or `weighted`.

- [ ] **Step 1: Failing tests.** On `_page_project` A: no `variable-actions:*` marker remains; `add-plot:0` opens a row under plot 0's header (not a dialog: no `ui.dialog` opened), Add stages `add /metrics/children/-` and the new criterion's editor is open under its row in plot 0; with a root `warn` the `+` is disabled with the reason; a plot whose series are all plot-only has no header `+`. Playwright: the `+` opens the row with a real mouse, Add, and the editor is visible in the viewport.
- [ ] **Step 2: Implement.** Mutants: `added` not passed (the editor does not open); the `_NO_ADD` gate removed at VARIABLE.
- [ ] **Step 3: Commit** "plot card: a criteria header's + opens the add row inline and the new editor after Add".

---

## Part 3 — the preview

### Task 7: The entry diff

**Files:** Create `src/dstf/authoring/spec_preview.py`; Test `tests/test_spec_preview.py`.

- [ ] **Step 1: Failing tests.**
  - `entry_lines(before: dict | None, after: dict | None, context: int = 3) -> list[Line]` with `Line(kind, text)` and `kind` in `"same" | "add" | "del" | "gap"`: over `json.dumps(x, indent=2).splitlines()` of each side (`[]` for None), `difflib.SequenceMatcher(a=..., b=..., autojunk=False).get_grouped_opcodes(context)`; a `gap` line (text `…`) between two groups; equal lines `same`, replaced lines their `del` lines then their `add` lines. A tolerance change `0.01 → 0.005` inside a three-level tree yields exactly one `del` `"tolerance": 0.01` line and one `add` `"tolerance": 0.005` line with at most 3 `same` lines each side; appending a child after it also yields `-  }` / `+  },` as the board drew; `before=None` is all `add`; equal entries give `[]`.
  - `spans(text: str, texts: Mapping[str, VariableText]) -> list[tuple[str, bool]]` (`(fragment, muted)`): each JSON string token of the line (`"(?:[^"\\]|\\.)*"`, unescaped with `json.loads`) that is a key of `texts` with `label != name` becomes `(label, False), (" " + name, True)` with the quotes dropped; every other fragment is kept verbatim; concatenating a line with no such token gives the line back. `"variable": "unitTests.x[1]",` with `{"unitTests.x[1]": VariableText(label="pulse.y", ...)}` gives `[('"variable": ', False), ('pulse.y', False), (' unitTests.x[1]', True), (',', False)]`.
  - Run: FAIL.
- [ ] **Step 2: Implement** (stdlib only). Mutants: `autojunk=True` on a 300-line entry with repeated lines (write the test that tells); the gap line dropped.
- [ ] **Step 3: Commit** "spec preview: an entry's diff as test_spec.json would write it".

### Task 8: The pending drawer is the preview

**Files:** Modify `src/dstf/authoring/shell/header.py`, `shell/style.py`; Tests `tests/test_shell.py`, `tests/test_test_page.py`, `tests/test_author_test_page_playwright.py`.

`PendingDrawer` (width 520 px): a heading `N pending edits · T tests` (`drawer-summary`); per test (in `spec.grouped()` order) a section (`drawer-test:<model>`) with the model id (`drawer-model`), `plural(k, "edit")`, the verdict phrase from Task 1's EDITS rule without the count (`verdict would change PASS → FAIL` / `verdict stays PASS` / `a new entry, scored PASS`, marker `drawer-verdict`), and the diff (`drawer-diff`: one monospace line per `Line`, `add` green and `del` red from theme tokens, `gap` muted; each line's spans with the muted name in the muted colour; `white-space: pre`, horizontally scrollable, lines prefixed `+ ` / `- ` / two spaces). The `before` is `spec.saved_entry(m)`, the `after` `spec.entry(m)`; the texts `session.variable_texts(m)`. At the bottom **Discard these** and **Save these** (`drawer-discard`, `drawer-save`) that call the header's own `_discard` / `_save` (the stale-file dialog included). `show(model_id)` opens the drawer and scrolls that test's section into view (the strip's Preview and save). Empty: `nothing staged`. The `drawer-op` lines go.

- [ ] **Step 1: Failing tests.** `tests/test_shell.py::test_the_pending_drawer_lists_staged_ops_by_model` rewritten: after staging a tolerance change on A the drawer shows `drawer-test:FakeLib.A`, a `del` line containing the old tolerance and an `add` line with the new; `drawer-verdict` reads per Task 1; `drawer-save` writes the file (compare the file's bytes to `json.dumps(effective, indent=2) + "\n"` for the entry) and the drawer then reads `nothing staged`. On a variable with an expression (build the fixture's entry with a `UnitTests`-style name mapped to an expression through `variable_texts`, or monkeypatch `Session.variable_texts`), the diff shows the label and the muted name. The strip's `strip-preview` opens the drawer with A's section in view (Playwright, scrollIntoView checked by bounding box). LOOK at 1280 with the drawer open.
- [ ] **Step 2: Implement.** Mutants: `before` read from `effective`; `Save these` saving without the stale check.
- [ ] **Step 3: Commit** "header: the pending drawer previews each test's entry before Save".

---

## Part 4 — one control set on both drawers

### Task 9: The matcher gets its own module; drawer state per tab

**Files:** Create `src/dstf/authoring/needle.py`; Modify `variable_rows.py`, `shell/variables_drawer.py`, `shell/tabstate.py`, every importer of `matcher` / `REGEX_BUDGET` / `REGEX_WALL_BACKSTOP`; Tests moved with them.

- [ ] **Step 1 (refactor commit, no behaviour change):** move `matcher`, `_glob`, `REGEX_BUDGET`, `REGEX_WALL_BACKSTOP` to `authoring/needle.py`; update importers and the tests that import them (`tests/test_variable_rows.py` → `tests/test_needle.py` for those tests). Full not-playwright suite green. Commit "needle: the budgeted filter matcher gets its own module" — its own commit.
- [ ] **Step 2: Failing tests** for `TabState`: `tests_regex`, `tests_case` (False), `tests_sort` (`"package"`), `tests_grouped` (True), `tests_short` (True), `tests_pills` (empty set), `vars_needle` (`""`), `vars_regex`, `vars_case` (False), `vars_group` (`"component"`), `vars_names` (`"both"`), `vars_sort` (`"file"`). A Variables drawer's needle and toggles survive switching test (they are read from and written to `TabState`, no longer the drawer instance). Run: FAIL. Implement; PASS.
- [ ] **Step 3: Commit** "tabstate: the drawers' controls are the tab's".

### Task 10: The Tests drawer's control set

**Files:** Create `src/dstf/authoring/shell/drawer_controls.py`; Modify `shell/test_list.py`, `shell/rail.py`; Tests `tests/test_test_page.py` (`TestRail`), `tests/test_author_test_page_playwright.py`.

`drawer_controls.py` holds what both drawers draw: `filter_box(needle, regex, case, on_change, placeholder, marker)` (the input with `.*` "Regular expression" and `Aa` "Match case" toggles inside it, `aria-pressed`, as the Variables drawer draws them today — move that code here) and `menu_button(icon, tip, options, value, on_change, marker)` (an icon button, tooltip `tip`, opening a menu of `role=menuitemradio` items with the current one checked). The Tests drawer, top to bottom: the filter box (placeholder `filter tests`; `needle.matcher` over the model id — substring, glob, regex with the budget; a bad pattern or a timeout says why below the box, as the Variables drawer does); a controls row — **sort** (`sort`, "Sort": Package order / Name A→Z / Status, failures first), **group** (`account_tree`, a toggle: "Group by package" / "Show flat"), **names** (`label`, "Names": Short / Full); compact status pills `PASS n · FAIL n · SIM_FAIL n · NO_REF n · EDITED n` (small, `aria-pressed`, counts over every test whatever the filter, zero-count pills disabled; selected statuses OR'd, EDITED OR'd with them — the Suite's rule, `suite_grid.js:53-56`); then the list. A row gains an `N unsaved` pill (`rows.EDITS` text) when the test has pending ops. "Status, failures first" orders by `reporting.status.STATUS_ORDER` then package order. Every control writes `TabState` and re-filters / re-orders in place (no page rebuild).

- [ ] **Step 1: Failing tests** (user simulation, `drawer_shell`): each control narrows or reorders the rows as described; the pills' counts ignore the filter; EDITED shows only edited tests and ORs with FAIL; `.*` with `^FakeLib\.A$` leaves A; a bad regex says `not a regular expression: …` and hides nothing; switching test keeps every setting. Playwright: menus open and choose with a real mouse; LOOK at 1280 with the Tests drawer open.
- [ ] **Step 2: Implement.** Mutants: the pills counting filtered rows; EDITED AND'd; sort not reapplied after a `results` event.
- [ ] **Step 3: Commit** "tests drawer: filter toggles, sort, group, names and status pills".

### Task 11: The Variables drawer's control set

**Files:** Modify `src/dstf/authoring/variable_rows.py`, `shell/variables_drawer.py`; Tests `tests/test_variable_rows.py`, `tests/test_test_page.py` (`TestVariablesDrawer`), `tests/test_author_test_page_playwright.py`.

`variable_groups(..., group="component" | "flat" | "tracked", order="file" | "name")`: `component` is today's grouping; `flat` is one group `All variables` (rank 1) over every kept name, families still folded; `tracked` is `Tracked` (rank 0) then one group `Other variables` (rank 1) with everything else, diagnostics included. `order="file"` keeps first-appearance order (today); `order="name"` sorts a group's rows by `text` (casefold, then text). `VarRow` gains `file_text`: the row read by result-file names (`family_label(key, members, {})`, or the name for a lone variable). The drawer's controls row under its filter box: **sort** (`sort`: Result-file order / A→Z), **group** (`account_tree`: By component / Flat / Tracked first), **names** (`label`: Expression / Result-file name / Both). Names: Expression shows `text` with no muted name; Result-file name shows `file_text` with no muted name; Both is today's row. The filter still asks both name and label. With `order="name"` under Result-file name, rows sort by `file_text`.

- [ ] **Step 1: Failing tests.** Unit tests for the three groupings and two orders over a fixture of names with a tracked family, a diagnostics name, a top-level name and two components; `file_text` of a folded family. Page tests: each menu changes the rows (names mode switching `unitTests.x[1]` / its expression; use a fixture where `variable_texts` gives a label different from the name), and the setting survives a test switch. LOOK at 1280.
- [ ] **Step 2: Implement.** Mutants: `tracked` grouping dropping diagnostics; `order="name"` sorting by the name under Expression.
- [ ] **Step 3: Commit** "variables drawer: sort, group and names menus".

### Task 12: Record it; the gate

- [ ] `docs/decisions.md` **D139 — The Test page: a status strip, one add model, a Save preview and the drawers' one control set (2026-09-27)**: the rulings (Q2 A, Q3 A, Q6 A, Q7), what each reverses (DESIGN.md Buttons; D131's dialog for VARIABLE — VARIABLE is now inline; D133's kind in the Edit form and Unwrap there), `Capability.emphasis` and `Panel.lead` as contract additions, `saved_comparison` (uncached, and what the gate measured), the planner's choices 1-5 above. `docs/usage.md` (the author section): the strip, the `+`, the kind dropdown, the preview drawer, the drawers' controls. `docs/ideas.md`: the drawer's per-edit sentences (choice 4), watch-only. `CLAUDE.md`: `authoring/` gains `situation.py`, `spec_preview.py`, `needle.py`; the shell line gains `strip.py`, `drawer_controls.py`; the "dstf author" comment block in "Running the Tool" says the strip, the inline `+`, the kind dropdown and the preview. Roadmap step T: BUILT with this plan's name and the decision. Full gates; commit.
- [ ] The controller runs an executing review of this plan BEFORE Task 1 (building every task in an archive), a mutation review per task or batch, and the TRANSFORM gate over a COPY: the strip on a PASS test, the SIM_FAIL test (`SteamWater_HCSG`), and a PASS test with a staged edit (a tolerance tightened until it FAILS: `verdict would change PASS → FAIL`, measured `saved_comparison` time); the kind dropdown and the plot `+` on a staged tree (every TRANSFORM leaf is nrmse — stage a tree in memory); the preview drawer on that edit with a UnitTests variable reading its expression and muted name; both drawers' controls over 331 tests and a test with thousands of variables; thumbnails read at 1280 and 900; Discard; owner files hashed before and after (spec md5, reference listing, work-dir listing).

---

## Corrections 1 (executing reviews, 2026-09-27) — these OVERRIDE the task text above

Two executing reviews built the plan in archives: Tasks 1-6 in `/tmp/claude-1000/planrev-T-a/arc` (working diff `.soul/planT-review-a.diff`; probes in `/tmp/claude-1000/planrev-T-a/probe/`), Tasks 1 (minimal) and 7-11 in `/tmp/claude-1000/planrev-T-b/arc` (`.soul/planT-review-b.diff`; probes in `/tmp/claude-1000/planrev-T-b/probe/`). Both ended green (not-playwright suite, the author Playwright files, ruff, format, bare mypy). An implementer MAY read the matching diff for its task; the plan and these corrections stay the spec — where a diff and a correction differ, the correction wins. Measured: `saved_comparison` ~12 ms per call on a TRANSFORM copy (29-32 ms worst; 8 ms of it is re-merging the 331-test spec); the Variables drawer rebuild at 6,011 names 188-280 ms in every group × order × names mode (today's default 238 ms).

**Owner rulings (2026-09-27):**
- **O1 — no simulation-settings state.** `/simulation` is not stageable (`patch_apply.DEFAULT_WHITELIST` is `/comparison`, `/metrics`, `/plots`; widening it changes `spec-update`, a seam TRANSFORM-UnitTests relies on). Remove `SIM_EDITS`, `touches_simulation`, the `sim_edits` input and the SIM_EDITS keys of both emphasis maps from Tasks 1-3, and planner's choice 5. D139 records that this ruled state waits for a simulation-settings editor.
- **O2 — the preview diffs against the tree as scoring sees it.** When a test's pending ops begin with the materialization `Session.stage` prepends (a flat entry, or no entry, whose first per-leaf edit writes the implicit tree out), the drawer's `before` is the saved entry (or `{"model": m}` for a new entry) WITH that materialization applied, and the section gets one note line (`drawer-note`): `Save also writes this test's N criteria out as a tree; they score the same.` (N = its leaves; for a new entry prefix `A new entry for this test. `). Build it so: `PatchOp` gains `materializes: bool = False` (not part of `raw()`); `stage` sets it on the op it prepends; convention 8's collapse of a later op onto a materializing op DROPS the flag (the value is no longer the scored tree); `SpecDocument.preview_base(model_id)` applies the model's materializing ops to the saved entry. Test on `_project` (flat) with a LOOSEN: the diff is the tolerance's `-`/`+` pair only, the note present; without a materialization no note.

**HIGH**
1. **(T8) The preview is built only while the drawer is open.** `Header.refresh` runs on every event (the 1 s `poll` during a job included); a drawer that rebuilds there broke `test_the_page_costs_the_same_whether_it_has_five_cards_or_forty[5,40]` and reset every diff's sideways scroll. Refresh the body from the drawer's `on_value_change` when it opens and from `show()`; while open, rebuild only when `(tuple(spec.pending), spec.content_hash, tuple((m, id(comparisons.get(m))) for m in edited))` moved. Merge the saved spec ONCE per rebuild for all edited tests (add `Session.saved_comparisons(models) -> dict`; `saved_comparison(m)` calls it). Tests: a closed drawer calls `saved_comparisons` 0 times; a `poll` rebuilds nothing; tests click `pending` before looking for drawer markers.
2. **(T6) Files and tests.** Add `tests/test_capability_conformance.py` (`test_a_variable_capability_is_a_captioned_button_under_the_plot` → an inline panel in the criteria header, with the card's hook). Rewrite: `test_a_card_reads_the_label_and_is_keyed_by_the_name`, `test_the_footer_adds_a_criterion_for_the_variable`, `test_a_no_baseline_test_draws_its_result`, `test_a_variable_keeps_its_card_when_its_last_criterion_goes`, `test_the_variable_footers_share_one_wrapping_row`, `test_two_plot_only_series_say_so_in_the_plural_and_get_no_footer`, `test_the_variable_footer_follows_the_job_that_locked_the_test`, `test_a_rebuilt_card_gives_up_its_hook`, `test_two_variables_sharing_one_expression_stay_tellable_apart`, builtin `test_the_add_form_offers_a_variable_with_an_awkward_expression`, `test_the_variable_panel_adds_a_criterion_for_it`, and the Playwright footer step (5d). Make the vacuous `_none("variable-actions:2:…")` real. Markers: the host is `variable:<index>`, the button `add-for:<variable>` (the capability does not know the plot index), `aria-label` = its tooltip.
3. **(T5) Files and tests.** `tests/capability_host.py`'s `HostPanel` gains `lead()`. Rewrite the 11 tests the kind move breaks: `test_the_root_has_no_remove_and_a_combinator_has_edit`, `test_unwrap_lives_inside_edit`, `test_unwrap_in_edit_stages_and_closes_the_row`, `test_the_edit_form_is_prefilled_and_stages_an_edit`, `test_edit_carries_unwrap_and_the_nodes_direction`, `test_the_edit_rows_buttons_read_unwrap_cancel_apply`, `test_unwrap_is_disabled_on_a_root_with_several_children`, `test_edit_to_weighted_waits_for_a_threshold_too`, `test_a_refused_form_keeps_its_row_and_what_was_typed`, `test_a_refused_unwrap_keeps_the_edit_row`, page `test_a_repainted_row_says_the_new_readout_the_new_text_and_the_new_k`; Playwright `test_a_form_row_opens_under_its_node_and_nothing_covers_it` (the root `and` has no tune icon now — use a k-of-n node).
4. **(T7) The diff shapes the plan asserted are not what difflib gives.** Appending a child after a changed leaf gives `- "tolerance": 0.01`, `+ "tolerance": 0.005`, `+ },`, `+ {`, the new block's lines as adds, and the old `      }` as context — assert exactly that (the archive's `test_appending_a_child_after_the_last_one_reads_as_difflib_aligns_it`). The autojunk test: 60 leaves cycling metric `i%3` and variable `x[1+i%4]` at one tolerance, tightened at leaves 10 and 50 — 4 changed lines with `autojunk=False`, ~201 with it on.
5. **(T8) The test as written cannot pass** (a LOOSEN on `_project` is all adds). Stage the LOOSEN, `save()`, then stage `replace /metrics/children/0/tolerance 0.1`: `- "tolerance": 0.5` / `+ … 0.1` and `verdict would change PASS → FAIL`. Markers per line: `diff-add`, `diff-del`, `diff-same`, `diff-gap`; the muted name `diff-muted`; the count `drawer-count`.

**MED**
6. **(T8) Sideways scroll inside the drawer.** `.dstf-diff { width: 100%; min-width: 0; max-width: 100% }`, `min-w-0` on the section and body columns, lines inside a `width: max-content; min-width: 100%` wrapper so the tints span the scrolled width. Under 1024 px Quasar's right drawer becomes an overlay over the header's Save: keep that (planner's choice — the drawer carries its own Save these).
7. **(T2) `_gone` and the new rebuild key.** Add `ActionBar.clear()` resetting the key; `_gone` calls `strip.forget()`, which calls it (`test_a_test_that_comes_back_is_drawn_again` must stay green). Task 2's tests use `test_action_icons`' `shell` and `_bar` (an ActionBar needs a Shell; `capability_host` has none). `ui.menu_item` has no `set_text`: draw its text in an `item_section` label. The strip's ⋮ has NO tooltip (it covered the open menu's first item) — `aria-label="More actions"`, as the header's ⋮.
8. **(T1/T3) `has_result`.** A never-run or unfinished test has a `TestResult(success=False)`. `has_result = result is not None and model_id not in storage.batch_manifest.unfinished_run_models(...)` (what Accept's "re-run before accepting" reads). A `simulate_only` test that ran gets its own sentence, grey: `Simulate-only: it runs, and nothing is compared.` (situation `SIMULATE_ONLY`, no emphasis).
9. **(T3/T5) Order rules.** `StatusStrip.paint` sets `situation` BEFORE `actions.refresh()`; `sync` reads `tree_view` once and passes it to `_paint(view)`; in `TreeCard.refresh` run `host.follow()` BEFORE painting; `_Row.paint` picks the count text only when the lead has children.
10. **(T5) Unwrap in the dropdown.** Omit the `unwrap…` option where `act` refuses it by rule (a root with several children) — a select option cannot carry a why-tooltip. Render the separator through the option slot (`<q-separator v-if="props.opt.label.startsWith('unwrap')"/>` + `q-item`). A `warn` root cannot be staged (the validator refuses "a tree of warnings alone"): T6's grey-`+` test uses a `weighted` root of two ranges.
11. **(T6) The plot `+` follows the root's kind.** The card stores the root combinator and rebuilds its VARIABLE host when it changes (the card's shape does not include it). CSS `.dstf-criteria-head > .dstf-node-host { margin-left: auto }`.
12. **(T6) Delete what nothing uses** (the stage's rule): `ActionBar(figure=)`, `_release`, `_owner` — move the `_barred` harness tests to TEST without a hook — and EditTree's VARIABLE caption branch with its tests.
13. **(T10) "Status, failures first"** orders FAIL, SIM_FAIL, scoring failed, NO_REF, PASS, unscored, then package order (`STATUS_ORDER` lists PASS first). The Tests drawer caches its kept set on `(needle, regex, case, tuple(ids))`; events repaint rows and reapply the cached set (a pathological regex cost 0.79 s CPU per pass over 331 ids, every second during a job). On a bad pattern or a timeout the Tests drawer hides NOTHING and says why. `.*` / `Aa` sit BESIDE the input, as today. Pills reuse the `.dstf-pill-<status>` tokens. `reporting.rows.unsaved_text(n)` gives `N unsaved` for both the Edits column and the row pill.
14. **(T10/T11) Menus by keyboard.** Menu items are `role="menuitemradio"` with `aria-checked`; add an ArrowUp / ArrowDown / Home / End handler moving focus among them (Quasar's QMenu has none) and a Playwright keyboard test; `aria-hidden="true"` on the check icon.
15. **(T11) `file_text`** is `", ".join(family_label(k, m) for k, m in group_families(members))` (folding over NAMES; the plan's formula folded over expressions and named `pipe.T[1:2]` for `unitTests.x[1:2]`) — test with x={pipe.T[3], pipe.T[5]} expecting `unitTests.x[1:2]`. `variable_groups` gains `read="expression" | "file" | "both"` so `order="name"` sorts by what is shown; test with `unitTests.x[1]` reading `a.Q` beside `b` and `m`.

**LOW**
16. (T9) Tests import `from dstf.authoring import needle as needle_module` (ruff F402 against `needle` locals); monkeypatches retarget `REGEX_BUDGET`, `thread_time`, `monotonic` there.
17. (T1) Counts agree with their verb (`capabilities.plural` + "change"/"changes" — moot after O1, keep the rule for any count sentence).
18. (T12) D139 states that "N unsaved edits" counts ops, so the first criterion edit on a flat test reads 2 (the materialization + the edit), as the header's count does.
