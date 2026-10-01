# The Tree Card's Structure (step U5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Test page's tree card shows where every row sits (a coloured bar per level, a `root` tag), lets a person fold any group away (large chevrons, Expand all / Collapse all, a folded group saying what it hides), and makes its rows act like the plots' criteria rows: a click on a criterion opens its editor under the row, a click on a `k-of-n` / `weighted` row opens its fields, and a ⤓ icon on each criterion jumps to where it is drawn.

**Architecture:** Everything here is the page's VIEW of the tree — no scoring, spec or patch change. Each tree row carries its node path and depth as data attributes; the level bars, the hovered branch's deeper shade, folding and the drag's hover-to-open are done in the page by one small script over those attributes, so folding never rebuilds rows (a rebuild bumps the U4 revision token and would refuse a drag in progress). The server keeps which groups are folded (per tab) and repaints the folded rows' summaries. A criterion's editor opens under its tree row through the same `EditCriteria` capability the plots use, at a new placement where only it attaches; one editor stays open page-wide, so the tree and the plot never hold two forms on one criterion.

**Tech Stack:** Python 3.10+, NiceGUI 3.16 (`[author]`), a small inline JS (as the U4 drag), pytest + pytest-asyncio, Playwright + Chromium, uv.

**Owner rulings (the tree-card grill, 2026-10-01, board https://claude.ai/artifact/V3Z34ChVybZgmzH8qWBmNh v2, confirmed):**
- **Q1 (c)** — one 6 px bar per LEVEL at the far left of each row (gap 2 px), coloured by level: level 1 blue, 2 violet, 3 cyan, 4 slate, then the cycle repeats; never green / red / amber (pass / fail / warn). The hovered group's bar turns a deeper shade of its own colour on the group and its descendants. Chevrons are 2-3x larger than today's (a 28 px target, an 18 px glyph). Chosen over the controller's recommendation (connector lines) after both were drawn.
- **Q2** — a muted `root` tag on the root row ("and · 5 criteria · root").
- **Q3** — a chevron on every group row; everything expanded by default; Expand all / Collapse all in the tree card's header.
- **Q4** — a folded group's row adds "· N hidden · x pass · y fail" (and "· z advisory" when there are any): its descendants' criteria counted by verdict.
- **Q5 (c)** — a click on a criterion row opens its editor under the tree row; a ⤓ icon on each criterion row jumps to it (Q7).
- **Q6 (b)** — a click on a `k-of-n` / `weighted` row opens its fields form (k; weights, threshold, direction) under the row; the "Change this combinator's fields" (`tune`) icon goes; the inline kind dropdown stays; a click on an `and` / `or` / `warn` row does nothing.
- **Q7 (a)** — the ⤓ jump scrolls to the criterion's row on its plot and flashes it; no editor opens or moves. A statistics criterion's ⤓ goes to its Statistics card row (opening the card when closed).
- **Q8 (a)** — which groups are folded is kept per browser tab (as the drawers' settings), per test, and follows a group through a move; a folded state is dropped when its group is deleted.
- **Q9** — a jump, an add, a move landing, or a refusal inside a folded group opens just the path to it.
- **Q10 (b)** — while dragging, resting ~0.7 s on a folded group opens it; a drop onto it still appends (U4).
- **Q11 (a)** — one editor page-wide: opening a criterion in the tree closes it on its plot and vice versa; the other place's readout and the plot update live as you type.
- **Q12** — a criterion with on-plot handles shows an "Edit on its plot" link in its tree editor, which moves the editor to the plot's row and scrolls there.
- **Q13 (a)** — the tree header's named jump buttons keep opening the criterion on its plot.

**Controller's calls (said to the owner; reversible):**
- (Y1) Level colours as tokens in `reporting/theme.py`'s table (`--dstf-level-1..4` and a deeper `--dstf-level-1-on..4-on`); light values `#7fb0ec`, `#a990dc`, `#5fbfcf`, `#9aa8b8` and deeper `#2f7fd8`, `#7650c6`, `#1f97ab`, `#6b7b8f` (the board's). The root row draws no bar.
- (Y2) "The hovered group": hovering a GROUP row deepens that group's bar on itself and every descendant row; hovering a criterion row deepens its parent group's bar likewise; the root highlights nothing. Done in the page (no server round trip).
- (Y3) Today's 16 px indent is replaced by the bars: a row's content starts after its bars (depth × 8 px) plus the chevron slot (28 px, empty on a criterion so pills line up).
- (Y4) The editor in the tree is the plots' editor (`EditCriteria`) at a new placement `Placement.TREE_CRITERION` where only it attaches (Edit on plot needs a drawn figure); `TestPage.open` gains the sentinel `TREE = -2` beside `STATS = -1`.
- (Y5) The fields form on a group row is the capability's (the `tune` icon's `build_edit`): a new `Panel.on_click(handler)` lets a capability say what a click on its row does; the tree card wires the row's click to it (the shell names no capability, as U4's `movable`). The kind dropdown, the grip and the icon cluster stop the click from reaching the row.
- (Y6) Folded state lives in `TabState.tree_folded: dict[str, list[str]]` (model id → folded group paths); a move re-keys it through `Moved.path`; any other edit keeps a path only while it still names a group.
- (Y7) Expand all / Collapse all act on every group of the test; Collapse all leaves the root open (it is the tree).

**Not in U5:** the plots' criteria rows (unchanged); the report tier (unchanged); moving rows by keyboard.

**Evidence (read at afba5a1, a read-only code map):**
- `authoring/shell/tree_card.py`: `_row` (:493-564) builds `ui.row().classes("dstf-tree-row …")` with inline `padding-left: {16*depth}px; --indent: …` (:502) and NO click handler; contents: grip span (`.dstf-grip`, 20 px), Pill, lead span (groups), label, a criterion's mono readout, `ui.space()`, the host column; the root row is `data-root="1"`. Five drag handlers on the rows column (:285-289); the move flash keyframe `dstf-flash` (style.py:118-121); `TreeCard.is_open` / `toggle` / `forget`; `_line` (:447-477) draws the header (toggle, root pill, text, tally, up to two named failure buttons → `page.jump_to`).
- `InlineHost` stops click propagation on its own column (`shell/actions.py:412`).
- Plot criteria rows: `self.row.on("click", partial(card.page.row_click, index, path))` (plot_card.py:183) → `TestPage.row_click` (:390) → `toggle_criterion` (:419) → `open_criterion` (:450-492; one editor page-wide: `_close_row()`, `_close()`, `self.open = (index, path)` or `(STATS, path)`); `PlotCard.open_editor` (plot_card.py:843-878); `StatsCard.open_editor` (stats_card.py:442-485, `Placement.STATISTIC_CRITERION`, `figure=None`).
- `EditCriteria` (builtin/edit_criteria.py:76) needs one test and a `leaf_path`; `EditOnPlot` needs a figure (`_NoFigure` raises, actions.py:60-64) and `sel.variable`; `_has_handles(metric)` (edit_on_plot.py) says whether a metric has on-plot handles.
- `EditTree._cluster` (builtin/edit_tree.py:661-858): the `tune` icon (`edit:{path}`) only for `_NEEDS_FIELDS = ("k-of-n", "weighted")` opens `build_edit` with `fixed=` the kind (k; weights, threshold, direction); the kind dropdown `_kind_select` (:1125) opens the same Edit row preset when k-of-n / weighted is chosen.
- `TestPage.jump_to` (:541-552) opens on the plot and scrolls `.dstf-crit-open` to centre; no flash.
- `TabState` (shell/tabstate.py:18-42) in `app.storage.tab["dstf"]`: survives navigation in a tab; no tree state today.
- No existing tree-node collapse anywhere; NiceGUI's QTree was rejected early (its server-side select does not fire `on_select`, plans/2026-09-09-the-shell.md:13).

---

## Rules for every task (the project's build loop)

- Another agent may be alive in the repo: commit ONLY with `git commit -F <your own msg file in your own scratchpad subdir> -- <explicit paths>`; before each commit run its tests and CONFIRM they were collected and passed, then `ruff format --check`; never `--amend`, never `git stash` (not even in a one-liner), never `git checkout -- <path>`, never switch branches. Never push. No `.soul/` record id in a tracked file.
- FOREGROUND commands with `cd /mnt/d/Modelica/DynamicSystemsTestingFramework &&`; each under 600 s (test_test_page.py whole is ~520 s — run it in halves; Playwright by test id); `uv run --no-sync`; kill only PIDs you started, never `pkill -f`; never port 8765; screenshots under the scratchpad; never `dstf run` / `compare` / `author` against a real work dir.
- Test-first; named mutants applied after the commit and restored (cp + cmp; `PYTHONDONTWRITEBYTECODE=1`).
- A UI change: a real browser, thumbnails READ (`~/.claude/skills/ue-windows-wsl/scripts/render_check.py thumb`); measure layout claims (boundingBox / computed style). A design choice not written here: stop and report.
- Gates before each commit: its tests, ruff check, ruff format --check; before a task's last commit also bare mypy, the whole not-playwright suite in chunks, the Playwright files it touched, tests/test_capability_conformance.py.

## File map

| File | Change |
|---|---|
| `src/dstf/reporting/theme.py` | T1: the level tokens. |
| `src/dstf/authoring/shell/tree_card.py`, `shell/style.py` | T1: rows carry `data-path` / `data-depth` / `data-group`; the bars; the root tag; the chevron slot; the hover script. T2: folding (script + server state + summaries + header buttons). T3: the row click. T4: the ⤓ icon. |
| `src/dstf/authoring/shell/tabstate.py` | T2: `tree_folded`. |
| `src/dstf/authoring/shell/test_page.py` | T2: open-the-path (Q9); T3: `TREE = -2`, opening and closing the tree editor; T4: the jump. |
| `src/dstf/authoring/capabilities.py`, `shell/actions.py`, `tests/capability_host.py` | T3: `Placement.TREE_CRITERION`; `Panel.on_click`. |
| `src/dstf/authoring/builtin/edit_criteria.py`, `builtin/edit_tree.py` | T3: EditCriteria at TREE_CRITERION (+ the "Edit on its plot" link); EditTree's `tune` icon goes, its row click opens the fields form. |
| docs | T5. |

---

### Task 1: Where each row sits

**Files:** `reporting/theme.py`, `authoring/shell/tree_card.py`, `authoring/shell/style.py`; tests `tests/test_test_page.py`, `tests/test_author_test_page_playwright.py`.

Each row gets `data-path`, `data-depth`, `data-group="1"` on a group; a bars element first in the row holding `depth` spans (`.dstf-lvl-1..4`, cycling `(i-1) % 4 + 1`, 6 px wide, 2 px gap, full row height); the chevron slot (Task 2 fills it; empty on a criterion); the root row a muted `root` tag after its text (Q2). The indent padding goes (Y3). A delegated `mouseover` on the rows column (the U4 drag script's file) adds `.dstf-branch-on` to the hovered group's row and every row whose `data-path` starts with `<group path>/`, removing it from the previous set (Y2); `.dstf-branch-on .dstf-lvl-k:nth-child(<the group's depth>)` takes the deeper token. The drag's "before" line still starts at the content's left edge (U4 C15) — recompute `--indent` from the new layout.

- [ ] Tests: user simulation — a nested tree's rows carry the attributes and the right number of bars with the right level classes (cycling at depth 5); the root has no bar and the `root` tag. Playwright — computed bar colours per level equal the tokens; hovering a group deepens exactly its descendants' bar at its level and nothing else (a sibling group's rows unchanged); hovering a criterion deepens its parent's branch; the before-line's left edge equals the target row's content left (U4's test, updated). LOOK at 1280 and 900 (a 4-deep tree, long labels): no overflow, pills aligned. Mutants: level colour by group index instead of depth; the hover highlights the whole level (all rows at that depth) instead of the branch; the root draws a bar. Commit "tree card: a bar per level and a root tag".

### Task 2: Folding

**Files:** `authoring/shell/tree_card.py`, `authoring/shell/tabstate.py`, `authoring/shell/test_page.py`, `authoring/shell/style.py`; tests `tests/test_test_page.py`, Playwright.

- A chevron button (28 px target, 18 px glyph, `aria-expanded`, `aria-label` "Fold <group text>" / "Unfold …") on every group row but the root (the root folds with the card's existing Show/Hide). A click toggles the group: the page script hides every row (and its slot) whose `data-path` starts with `<path>/` — `hidden` attribute or a class, NO server rebuild; the server records it in `TabState.tree_folded[model_id]` (Y6) through one event; the folded group's row shows Q4's summary (server-rendered from the tree view: its descendant leaves counted by verdict).
- Expand all / Collapse all in the header line (Y7), as text buttons beside the tally.
- On (re)build the tree card applies the tab's folded set (the script hides the rows on mount; the server renders the summaries).
- Q9: `TestPage.open_path(node_path)` unfolds every folded ancestor of a node (and nothing else) whenever something is shown at a TREE row inside a folded group: a move's reveal and its "Moved." line, a refused act's sentence under a row, a form row or a tree editor opened under a row, an added criterion's reveal when it is revealed in the tree. The ⤓ jump and the header's named jumps go to plots / the Statistics card, not to tree rows, so they unfold nothing.
- Q8 / Y6: a move re-keys the folded paths (a folded group that moved is folded at its new path; folded groups inside the moved subtree likewise; paths that no longer name a group after any edit are dropped on the next sync).
- Q10: while dragging, the script starts a 700 ms timer when the pointer enters a FOLDED group row (either half); leaving the row cancels it; on expiry it unfolds that group client-side and tells the server (as a click would) — the rows are not rebuilt, so the drag's revision token stays valid (U4 W5); a drop onto a folded group (bottom half) still appends.

- [ ] Tests: user simulation — folding records the path in TabState and renders the summary ("3 hidden · 2 pass · 1 fail"); unfold removes it; Collapse all / Expand all; the folded set survives switching to another test and back (tab state) and a page reload in the tab; a move re-keys a folded group; a removed group's path is dropped; Q9 for each trigger (jump, add, move, refusal) opens only the path. Playwright — folded rows are hidden (and their slots), no rebuild happens on fold (the rows' revision token unchanged), a real-mouse drag resting 700 ms over a folded group unfolds it and a drop between its now-visible children lands there; a shorter rest does not unfold. LOOK at 1280 and 900: chevrons (measure 28 px), a folded row's summary, Expand / Collapse all. Mutants: fold rebuilds the rows (revision bumps); Q9 unfolds everything; the summary counts direct children only; the hover timer never cancelled; a re-key that drops a moved folded group. Commit "tree card: fold and unfold groups".

### Task 3: A click on a row

**Files:** `authoring/capabilities.py`, `authoring/shell/actions.py`, `tests/capability_host.py`, `authoring/builtin/edit_criteria.py`, `authoring/builtin/edit_tree.py`, `authoring/shell/tree_card.py`, `authoring/shell/test_page.py`; tests `tests/test_test_page.py`, `tests/test_builtin_capabilities.py`, `tests/test_capability_conformance.py`, Playwright.

- Criterion rows (Q5, Y4): a click toggles its editor under the row — `InlineHost(Placement.TREE_CRITERION, partial(Selection, tests=frozenset({m}), leaf_path=path), figure=None, ...)` in the row's slot, a Done button, `dstf-crit-open` on the row; `TestPage.open = (TREE, path)`; opening it closes any plot / Statistics editor and form row, and opening one of those closes it (Q11). `EditCriteria.placements` gains `TREE_CRITERION`; for a statistics leaf it already hides mode / window / quantity. Q12: when the leaf's metric has on-plot handles (`edit_on_plot._has_handles`) and its plot holds it, the editor shows "Edit on its plot" (a link button) → `page.open_criterion(path, <its plot index>)` + scroll (the tree editor closes by the one-editor rule).
- Live sync (Q11): an edit staged in the tree editor repaints the plot (its criteria row readout and its figure) through the existing sync, and the tree row's readout; test it.
- Group rows (Q6, Y5): `Panel.on_click(handler)` — the tree card wires the row's `click` to the handler the capability registered (None → the row click does nothing). `EditTree._cluster` registers, for `k-of-n` / `weighted`, a handler opening its `build_edit` row (`fixed=` the kind) — the same form the `tune` icon opened; the `tune` icon (`edit:{path}`) goes. `and` / `or` / `warn` register nothing. The kind dropdown, the grip, the chevron and the icon cluster stop propagation (a click on them never reaches the row).
- The root row: a click does nothing (it has no fields; its kind is its dropdown).

- [ ] Tests: a leaf row click opens the editor under the tree row (`page.open == (TREE, path)`), a second click closes it; opening its plot editor closes the tree one and vice versa; a tolerance typed in the tree editor stages one replace and the plot's row readout updates; "Edit on its plot" shown for a tube, absent for nrmse and for a statistics leaf, and it moves the editor; a k-of-n row click opens its fields (k), a weighted row's (weights, threshold, direction); an and/or/warn row click opens nothing; a click on the kind dropdown / grip / an icon does not open anything else; the `tune` icon is gone (`edit:{path}` marker absent); conformance green (the shell names no capability); `HostPanel` gains `on_click`. Playwright: real clicks for each; LOOK at 1280 and 900 (the editor under a tree row fits the card — U3's B12 rule). Mutants: the row click reaches through the dropdown; the tree editor stays open when the plot's opens; the link shown for nrmse; on_click wired on a plot's criteria row. Commit "tree card: a click on a row opens it".

### Task 4: The ⤓ jump

**Files:** `authoring/shell/tree_card.py`, `authoring/shell/test_page.py`; tests `tests/test_test_page.py`, Playwright.

A ⤓ icon button (`aria-label` "Show on its plot" / "Show on the Statistics card") at the start of each criterion row's icon cluster (the board drew it there), drawn by the tree card (the page owns jumping, as the header's named buttons). Q7: it scrolls the criterion's row on its plot into view (centre) and flashes it (`dstf-flash`), drawing the plot if it is lazy and not drawn yet, ending Focus if it hides that plot; no editor opens or moves. A statistics leaf: the Statistics card opens if closed, its row is scrolled to and flashed. Q13: the header's named buttons are unchanged.

- [ ] Tests: the ⤓ scrolls (Playwright: the plot row in view after the click, `.dstf-flash` on it) and opens nothing (`page.open` unchanged); a statistics leaf's goes to the card row (opening the card); a plot hidden by Focus is shown; a lazy plot is drawn. Mutants: the jump opens the editor; Focus kept hiding the plot. Commit "tree card: ⤓ shows a criterion where it is drawn".

### Task 5: Record it; the gate

- [ ] `docs/decisions.md` D146 (the rulings Q1-Q13; Y1-Y7; the reason folding is client-side — U4's revision token; what it removes — the `tune` icon, the 16 px indent), `docs/usage.md`, CLAUDE.md's `dstf author` block (one clause), the roadmap (a U5 row BUILT).
- [ ] The controller: two executing reviews before Task 1 (review a builds T1-T2 with a SPIKE first: the hover script and client-side folding over NiceGUI's row updates — does a server repaint of a row (a pill, a summary) undo a client-set `hidden` / class?; review b builds T3-T4), a mutation review per part and per fix batch, and the TRANSFORM gate over a COPY: BranchingDynamicPipes (flat) and a staged nested tree in memory — the bars, fold / unfold / Collapse all, a drag resting on a folded group, a leaf click editing in the tree with the plot updating live, a k-of-n row's fields, the ⤓ jump; owner files hash-identical.

## Corrections 1 (executing reviews + owner, 2026-10-01) — these OVERRIDE the task text above

Two executing reviews built the plan in archives: Tasks 1-2 with a SPIKE in `/tmp/claude-1000/planrev-U5-a/arc` (diff `.soul/planU5-review-a.diff`; probes `/tmp/claude-1000/planrev-U5-a/probe/` — `test_spike*.py`, `test_probe_folds.py`, `test_probe_pw.py`, `mut2.sh`), Tasks 3-4 in `/tmp/claude-1000/planrev-U5-b/arc` (diff `.soul/planU5-review-b.diff`; probes `/tmp/claude-1000/planrev-U5-b/probe/`). Both green (suite, author Playwright, ruff, format, bare mypy, conformance). An implementer MAY read the diffs; plan + corrections are the spec.

**The spike (review a), executed on the real tree card over eight repaint kinds (`Pill.set`, label text, `row.classes`, `row.props` + update, `row.update()`, an added child, `rows.update()`, `page.sync()`):** only `row.classes(...)` undoes client changes — it wipes EVERY client-set class on the row (`open_row` already calls it; Task 3 adds `dstf-crit-open`). Client-set ATTRIBUTES (`hidden`, `data-*`) and inline style survive all eight; `hidden` hides a `.nicegui-row` (Quasar's `[hidden]` → `display: none`). A client-side unfold keeps U4's `data-rev` token valid (a real-mouse rest-and-drop accepted 3/3). The branch highlight survives repaints when it marks the BAR SPAN, not the row.

**Owner rulings (2026-10-01, drawn options):**
- **R1 — folds follow their group through EVERY tree edit** (remove / wrap / unwrap / add / kind change / move): each edit reports old → new paths for the nodes it keeps, and the folded set is re-keyed through that map; a folded group that is deleted loses its fold.
- **R2 — the tree unfolds a path only for TREE events:** a move's reveal and "Moved." line, a refused act's sentence under a row, a form row or a tree editor opened under a row. Things done outside the tree (an add from a plot's `+`, a criterion opened on its plot, the ⤓ and the header's named jumps) leave the folds alone; the folded group's summary updates ("4 hidden"). This narrows the grill's Q9 wording ("a jump") as the owner confirmed.

**Tasks 1-2 (review a):**
- E1 HIGH (R1) — `plan_move` returns `Moved.paths` (old → new for every kept node, from its origin identity map); `Panel.moved(node_path, text, paths)` carries it; `TestPage.moved` calls `TreeCard.rekey(paths)` before the sync (re-keying through `Moved.path` alone is wrong: a move shifts its siblings — `test_a_move_rekeys_the_folded_groups_it_shifts`). For the other EditTree actions (R1), the action computes its path map from the ops it stages (add at an index shifts later siblings; remove shifts them back; wrap pushes the node one level down to `<path>/children/0`; unwrap lifts children; a root-add wrap pushes everything under `/metrics/children/0`) — one helper in `tree_move.py` (`path_map(old_tree, new_tree)` by node identity is NOT available across a stage; build the map from the op semantics and test each action). The T2 file map adds `tree_move.py`, `capabilities.py`, `shell/actions.py`, `builtin/edit_tree.py`, `tests/capability_host.py`.
- E2 MED (the spike) — folding sets ATTRIBUTES only, never classes, on rows: `hidden` on hidden rows and their slots (`data-slot-of="<path>"` on a slot column), `data-folded="1"` on a folded group row; the server sets the same props on the fold event and at every build (a props update, not a rebuild; the revision unchanged — assert it); the branch highlight sets a class on the BAR SPAN (`.dstf-lvl-on` on bar k), not on rows; no static `nth-child(depth)` CSS (it cannot cover unbounded depth).
- E3 — reuse rows' existing `data-tree-path` (not a new `data-path`).
- E4 — row order: bars, chevron slot (28 px, empty on a criterion), grip, pill, …; U4's before-line `--indent` = the chevron slot's left edge = `8·depth + 12` px; `TreeCard.say`'s "Moved." indent becomes the same `pill_left(depth)`.
- E5 — tests the plan breaks: `test_test_page.py::test_a_rows_indent_is_its_depth_in_the_tree` (replaced), `::test_the_root_rows_kind_is_a_dropdown_and_its_text_the_count`, `::test_only_the_tree_rows_take_a_drag` (the grip is no longer the first child); Playwright `test_a_drag_before_a_row_reorders_the_tree_and_the_plots` (`line == "16px"`), `test_a_hidden_handle_keeps_its_place_and_a_lock_shifts_no_pill` (the pill step is now 8 px per level).
- E6 — Q4 counts CRITERIA (leaves, each once — nested folded groups counted once via `leaves_of`), plus "· z unscored" when some are; Collapse all closes a form or editor under a row it hides (the Hide-tree rule), keeps one under a folded group's own row; Q10's 700 ms timer cancels on leave / drop / dragend and clicks the chevron on expiry.
- E7 — trap: the root tag's separator is "·" (U+00B7) — write the character or `"·"`, never `"\00b7"` (that printed "�b7").

**Tasks 3-4 (review b):**
- F1 HIGH — the ROOT follows Q6 like any group: a click on a `k-of-n` / `weighted` root opens its fields (the plan's "the root row: a click does nothing" is deleted — with `tune` gone a k-of-n root would have NO way to edit k). `test_a_k_of_n_roots_click_opens_its_k`.
- F2 HIGH — "Edit on its plot" is drawn by the TREE CARD beside Done (a capability has no page; the shell may not import `edit_on_plot`): shown when some capability placed at CRITERION and not at TREE_CRITERION is `available is True` for `Selection(variable=<its plot row's variable>, leaf_path)`; repainted on each refresh (it goes when the mode switches to nrmse). Drop "+ the link" from `edit_criteria.py`'s file-map row.
- F3 MED — `jump_to` scrolled with `querySelector(".dstf-crit-open")`, which finds a TREE row once tree rows wear that class: scroll by element id — `TestPage._drawn_row(path)`; Task 4's ⤓ uses it too.
- F4 MED — the ⤓ button stops propagation (`js_handler="(e) => { e.stopPropagation(); emit(); }"`), and so does every tree-card-owned control on a row (chevron, ⤓, grip); in user simulation, assert in Playwright (user simulation never bubbles).
- F5 (controller's call, reversible — keyboard access, the `tune` icon was the only keyboard route to k / weights) — a row with a click (a criterion, a k-of-n / weighted group) gets `role="button" tabindex="0"`, Enter / Space fire its click, and `aria-expanded` mirrors whether its editor / form is open (the legend swatches' pattern). A Playwright keyboard test.
- F6 MED — every branch on `TestPage.open` handles `TREE = -2`: `_close` (a TREE branch; without it `self.cards[-2].close_editor()` ran on the second-to-last card), `rebuild` (`open[0] >= 0`, not `!= STATS`), `focus` (skip TREE), `TreeCard.refresh` (Hide tree closes the tree editor), `_build_rows` (calls `page._close()` — `test_a_rebuild_of_the_tree_rows_closes_the_tree_editor`), `InlineHost.clear` resets `row.click` (`test_a_locked_k_of_n_rows_click_opens_nothing`); unchanged: `editing_for`, `moved`/C11 (via `_close`), `_gone`, `jump_to`/`_reveal`, `open_row`.
- F7 MED — `Panel.on_click` gated by a `RowTarget.clickable` flag only the tree card sets (as `drag` / `can_move`); `HostPanel` draws a `host-click` button when a click is registered (14 `edit:{…}` sites in test_builtin_capabilities.py migrate to it).
- F8 — a click on a row that has a form open under it closes the form; a second click on a k-of-n row closes its fields; clickable rows show `cursor: pointer`; the ⤓ `aria-label` is repainted (not fixed at build — `shape_of` excludes the metric).
- F9 — the tree editor (Task 3) follows E6's Collapse-all rule and R2 (opening one unfolds its path).
- F10 — tests the plan breaks: test_builtin_capabilities.py `test_the_root_has_no_remove_and_a_combinator_has_its_kind`, `test_unwrap_lives_in_the_kind_dropdown`, `test_the_edit_form_is_prefilled_and_stages_an_edit`, `test_a_form_opened_after_the_session_moved_shows_the_node_as_it_is`, `test_a_form_is_rebuilt_on_every_open`, `test_edit_carries_the_nodes_direction`, `test_the_edit_rows_buttons_read_cancel_apply`, `test_an_unchanged_edit_stages_nothing_and_closes_the_row`, `test_a_refused_unwrap_keeps_the_edit_row`, `test_the_builtins_declare_their_placements`; test_test_page.py `test_a_discarded_edit_reopens_on_the_nodes_value`, `test_a_k_of_n_node_keeps_its_edit_icon_with_no_kind_select`, `test_a_greater_weighted_nodes_hint_says_so_at_once`, `test_a_tree_rows_icons_are_named_by_their_tooltips`; Playwright `test_a_combinators_kind_dropdown_opens_with_a_real_mouse`.
- F11 — markers: `tree-editor:{path}`, `tree-editor-done:`, `tree-editor-on-plot:`, `show:{path}` (the ⤓); the ⤓ icon `vertical_align_bottom` between `ui.space()` and the host.
