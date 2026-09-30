# Move a Subtree (step U4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In the Test page's tree card, any criterion or combinator (with its children) can be moved — by dragging its row's handle or by a "Move to…" menu — to a position between rows, into a combinator, or onto a criterion (which wraps both in `and`); the move keeps the tree valid by adjusting what it must and says what it changed, and a move that cannot be made is refused with the reason.

**Architecture:** A pure function, `authoring/tree_move.py::plan_move(tree, src, dest)`, computes the whole new tree (or a refusal) on a copy: take the subtree out, repair the source parent (drop an emptied combinator, lower `k`, drop the weight), put it in at the destination (insert, append, or wrap both in `and`, adding a weight of 1 under `weighted`), refuse what the owner's rules and the validator refuse. `EditTree` gains `action="move"`, staging ONE op — `add /metrics <new tree>`, the root add it already uses — so a move is one pending edit, needs no materialization, and changes no patch format. `move_destinations(tree, src)` lists every destination with its label and its refusal reason, for the menu and for the drag. The drag is plain HTML5 drag-and-drop on a handle, wired through a new `Panel.movable(...)` hook the tree card implements, so the shell still names no capability.

**Tech Stack:** Python 3.10+, NiceGUI 3.16 (`[author]`), a small inline JS drag handler, pytest + pytest-asyncio, Playwright + Chromium, uv.

**Owner rulings this plan implements:**
- Grill 2026-09-28 (Q4, Q9) and the plot-model board: move a subtree by drag AND "Move to…" in the tree card; a drop on a criterion wraps both in `and`; a refused drop says why. The board drew ⋮⋮ handles and a "Move to…" menu whose rows are the destinations, a criterion's greyed with "A criterion can't hold others. Drop on it to wrap both in `and`".
- 2026-09-30 (asked with drawn previews): **(D1)** drop zones are BETWEEN rows (insert at that position — this reorders plots, which follow tree order since D140) AND ONTO rows (onto a combinator: append at its end; onto a criterion: wrap both in `and`); Move to… lists the same destinations, positions included. **(D2)** a move that empties a combinator removes it too (a `warn` whose one criterion moves out disappears), repeatedly up the tree. **(D3)** `k-of-n` / `weighted` adjust and the row says what changed: out of a `k-of-n` with `k > n` → `k = n` ("k 3 → 2"); into a `weighted` → the moved node gets weight 1; out of one → its weight leaves with it. **(D4)** a move that puts a `warn` directly under an `or` or a `k-of-n` is REFUSED with D142's reason; only moves are checked, existing trees are left alone.

**Controller's calls (said to the owner; reversible):**
- (V1) A move stages one `add /metrics` op carrying the whole new tree (no RFC 6902 `move`: `patch_apply` supports `add` / `replace` / `remove` only, and `dstf spec-update` goes through it).
- (V2) Drag and Move to… live in the TREE CARD only (not on the plots' criteria rows, which also host `Placement.NODE`).
- (V3) A drop's zone on a row: the top quarter = before it, the bottom quarter = after it, the middle = onto it. The root row has only "onto" (nothing sits beside the root). A thin accent line marks before/after; an outline marks onto; a refused target shows `cursor: not-allowed` and its reason as the row's title while dragged over.
- (V4) After a move: every open editor and form row is closed BEFORE staging (a move that swaps two leaves on one variable keeps the page's shape and would rebind an open editor to the other leaf — see Evidence), the moved node's row is revealed (scrolled into view, flashed like a jump), and what the move changed ("Moved. k 3 → 2." / "Moved. The empty warn was removed." / "Moved. Weight 1 given.") shows as one line in that row's slot until the next action.
- (V5) Refusals, in this order, each one sentence: the root can't be moved ("The root holds the whole tree: move what is in it instead."); into itself or its own subtree ("A criterion group can't move into itself."); into a `warn` ("A warning holds exactly one criterion: drop on that criterion to wrap both in `and`."); D4 ("A warning passes whatever it scores: under or / k-of-n it would pass its parent with nothing else passing."); then the validator's own sentence (`parse_metric_tree` / `validate_tree` — e.g. no primary criterion left outside a `warn`). A destination that is where the node already is: listed as "where it is", greyed, no reason.

**Not in U4:** moving between tests; moving on the plots' criteria rows; undo beyond Discard (the pending edit is the undo); the report tier (unchanged).

**Evidence (read at 4f1464a, from a read-only code map):**
- `authoring/shell/tree_card.py`: rows are a FLAT list (`_build_rows` :274-280, `_row(node, depth)` :282-326, indent `padding-left: 16*depth`, marker `tree-row:{path}`), each followed by its slot column; `RowTarget(page, f"tree:{path}", row, path, None)` with `target.lead`; `InlineHost(shell, Placement.NODE, partial(Selection, tests=…, node_path=path), figure=None, marker=f"node:{path}", row=target)`; `shape_of` (:34-52) is `(path, kind, variable, combinator)` per node — a move changes it, so every row rebuilds. `Placement.NODE` is also hosted on each plot criteria row (plot_card.py:231-245).
- No drag-and-drop anywhere in the shell. NiceGUI 3.16's `make_sortable` (SortableJS) reorders ONE flat list and has no "drop onto" — it does not fit an indented flat list with interleaved slot columns. `Element.on(event, handler, args=..., js_handler=...)` (nicegui element.py:356-364) is used already (actions.py:365).
- `authoring/builtin/edit_tree.py`: `act` (:233-336) — the root add `add /metrics {"combinator":"and","children":[node, leaf]}` is the wrap-both precedent; multi-child `unwrap` (:296-306) is the only remove+add move today; `_cluster` (:633-826) draws `add:` / `wrap:` / `edit:` / `remove:` / `kind:` per node; `available` (:193-217) — one test, no job lock (`_locked_by`), a tree from `tree_or_reason`, the node exists; refusals reach the row's slot through `InlineHost.submit` → `user_text(e)` (actions.py:471-483).
- `discovery/patch_apply.py:50` `_SUPPORTED_OPS = {"replace","add","remove"}`; `discovery/spec_update.py::needs_materialization` is False when the patch supplies `/metrics` itself.
- `Session.stage` (session.py:1114-1209) validates the candidate (`validate_entry_metric_tree`, then `_merge`), collapses a single op with the same `(model_id, path)` as the last pending one — two moves in a row fold into one pending `add /metrics`.
- Validator sentences: `tree_spec._parse_combinator` (:464-560) — non-empty children, `warn` exactly 1 child, `k` whole ≥ 1 and ≤ n, weights one per child; `validator.validate_tree` (:47-73, :94-167) — a primary leaf outside `warn`, soft checks under `warn`, companions not targetable, weighted score directions. D142's warn rule is NOT enforced by the validator (seeds.py only).
- Path-keyed state: `PlotCard.rows` / `open_path` / `choices` (plot_card.py), `TestPage.open` (with `STATS = -1`), `form`, `pending_reveal`, `shape()` (test_page.py:769-791), the Statistics card's `row_for` / shape. Hazard: a move that keeps the list of `(path, variable)` (two leaves on `x` trading places) changes no shape, and an open editor at that path rebinds to the other leaf (plot_card.py:622-631) — V4 closes editors first.
- Tests to build on: `tests/test_builtin_capabilities.py` `NESTED` (:374-391), `_node`; `tests/test_test_page.py` `NESTED` (:1620-1635) + path constants, `_nested_project` / `nested_shell`; `tests/test_author_test_page_playwright.py` `_served`, `_icon`, the kind-dropdown real-mouse test (:2562).

---

## Rules for every task (the project's build loop)

- Another agent may be alive in the repo: commit ONLY with `git commit -F <your own msg file> -- <explicit paths>`, after `ruff format --check` passes (never chain a commit after a failed check); never `--amend`, never `git stash` (not even in a one-liner), never `git checkout -- <path>`, never switch branches. Never push. No `.soul/` record id in a tracked file.
- FOREGROUND commands with `cd /mnt/d/Modelica/DynamicSystemsTestingFramework &&`; each under 600 s (the not-playwright suite in chunks: `tests/test_test_page.py` alone, the scratchpad's `chunkA.txt` / `chunkB.txt`, and every test file in neither list); `uv run --no-sync`, never `uv sync`; kill only PIDs you started, never `pkill -f`; never port 8765; screenshots under the scratchpad; never `dstf run` / `compare` / `author` against a real work dir.
- Test-first; named mutants applied after the commit and restored (cp + cmp; `PYTHONDONTWRITEBYTECODE=1`).
- A UI change: spike the mechanism in a real browser FIRST, then gate in Chromium, then READ thumbnails (`~/.claude/skills/ue-windows-wsl/scripts/render_check.py thumb`). A design choice not written here: stop and report.
- Gates before each commit: the task's tests, `ruff check`, `ruff format --check`; before a task's last commit also bare `mypy`, the whole not-playwright suite, the Playwright files it touched.

## File map

| File | Change |
|---|---|
| `src/dstf/authoring/tree_move.py` (new) | T1: `Dest`, `Moved`, `plan_move`, `move_destinations`. |
| `src/dstf/authoring/builtin/edit_tree.py` | T2: `act(action="move", path=src, dest=...)`; T3: the Move to… menu; T4: the handle and drop wiring through `Panel.movable`. |
| `src/dstf/authoring/capabilities.py`, `authoring/shell/actions.py` | T4: `Panel.movable` (protocol + `ShellPanel`), `RowTarget.drag` set by the tree card only. |
| `src/dstf/authoring/shell/tree_card.py`, `shell/test_page.py`, `shell/style.py` | T4-T5: the drag JS and zones; closing editors, reveal, the "Moved." line. |
| docs | T6. |

---

### Task 1: The move, as a pure function

**Files:** `src/dstf/authoring/tree_move.py` (new); tests `tests/test_tree_move.py` (new).

```python
@dataclass(frozen=True)
class Dest:
    kind: Literal["at", "onto"]   # "at": into combinator `path` at `index` (None = append);
    path: str                     # "onto": onto the node at `path` (a criterion: wrap both in `and`;
    index: int | None = None      #          a combinator: append — the same as at(path, None))

@dataclass(frozen=True)
class Moved:
    tree: dict                    # the whole new tree (what `add /metrics` stages)
    path: str                     # where the moved node is in it (for the reveal)
    notes: tuple[str, ...]        # what else changed: "k 3 → 2", "the empty warn was removed", "weight 1 given"

class MoveRefused(ValueError):
    """`str()` is the one sentence the row shows (V5)."""

def plan_move(tree: dict, src: str, dest: Dest, *, validate: Callable[[dict], None] | None = None) -> Moved | None:
    """None when the move changes nothing (the node is already there)."""

def move_destinations(tree: dict, src: str, *, validate=...) -> list[tuple[str, Dest, str | None]]:
    """Every destination in tree order: (label, dest, reason-or-None). Labels:
    "and (root) — at 1", "… — at the end", "k-of-n · 2 of 2 — at 2", "nrmse · p[1] — wrap both in and",
    "… where it is" (reason None but greyed by the caller: plan_move gives None)."""
```

Algorithm (on `copy.deepcopy(tree)`): refuse per V5's first three before anything; give every node an identity (walk and tag `id()` of the copy's dicts) so the destination survives the removal's index shifts; remove `src`; repair its parent upward — `children == []` → remove the parent too (D2, repeat while the grandparent empties; if the ROOT itself would empty — only possible when `src` was its last descendant, which also means nothing is left to hold it — refuse with the validator's "non-empty children" sentence); `k-of-n` with `k > n` → `k = n` and a note; `weighted` → drop the weight at the removed index; put the node at the destination found by identity — `at`: insert (under `weighted` insert weight 1 at the same index + a note), `onto` a criterion: replace it by `{"combinator": "and", "children": [target, moved]}`; D4: after the insert, if the MOVED node is a `warn` whose new parent is an `or` or a `k-of-n` → refuse (only the moved node's own new position is checked: the subtree it carries is unchanged, and an `onto` wrap makes the parent an `and`); finally `validate(new_tree)` (the Session passes `parse_metric_tree` + `validate_tree` with the test's baseline roles) → a `MetricSpecError`/`ValidationError` becomes `MoveRefused(its sentence)`.

- [ ] **Step 1: Failing tests** (a table over `NESTED`-like trees; each asserts the new tree exactly, the moved path, the notes): reorder inside the root (1 → 3, 3 → 1, to the end); into a combinator at an index and appended; onto a criterion (wrap in `and`, target first); onto a combinator == append; out of a `warn` (the warn disappears; note); emptying a one-child `and` nested in an `or` (the `and` is removed; the `or` stays while it has other children) and emptying a chain (`or[and[x]]` under the root with `x` moved out: both go); out of `k-of-n 3 of 3` (k → 2; note); out of `k-of-n 2 of 3` (k stays); into `weighted` with weights `[2, 3]` at index 1 (weights become `[2, 1, 3]`; note); out of `weighted` (its weight leaves); refusals: the root; into itself; into its own child; into a `warn`; a warn at an `or` (D4); the last primary moved onto a criterion inside a `warn` (the validator's sentence); a move that changes nothing → None. `move_destinations` over a nested tree: order, labels, a refused destination's reason, "where it is". RED (ImportError).
- [ ] **Step 2: Implement; green.** Mutants: removal before resolving the destination by identity (a destination after the source shifts — the index test fails); an emptied parent kept; `k` not lowered; weight appended instead of inserted at the index; D4 checked on the source position instead of the destination; `onto` puts the moved node first. Commit "tree move: plan a move as a whole new tree, or refuse it with one sentence".

### Task 2: EditTree stages a move

**Files:** `authoring/builtin/edit_tree.py`, `authoring/session.py` (only if a validate hook for the test's roles needs exposing); tests `tests/test_builtin_capabilities.py`, `tests/test_session.py`.

`act(view, action="move", path=src, dest=Dest(...))`: `tree, why = s.tree_or_reason(m)` (refuse with `why`); `plan_move(tree, src, dest, validate=<the test's validator>)`; None → no outcome; `Moved` → `Patch(ops=[PatchOp(m, "add", ROOT, moved.tree)])` and the outcome carries `moved.path` and `moved.notes` for the page (use the outcome's existing message / reveal channel — read how `act` returns what the shell reveals and extend the smallest thing; say which). Refusals raise so `InlineHost.submit` shows the sentence under the row. The job lock refuses as every EditTree action does.

- [ ] Tests: a flat test's move stages exactly one op (`add /metrics`, no materialization op before it); two moves in a row leave ONE pending op (`stage`'s collapse); the pending count reads 1; the preview diff shows the reordered entry; a refused move stages nothing and says the sentence; a locked test refuses. Mutants: staging remove+add; materialization staged; notes dropped. Commit "EditTree: a move stages one whole-tree add".

### Task 3: Move to…

**Files:** `authoring/builtin/edit_tree.py` (the NODE cluster), `authoring/capabilities.py` / `shell/actions.py` (only the V2 gate: the cluster draws Move to… where the host offers moving — the same `ui_panel.movable` availability Task 4 adds; add it here as a read-only property first), tests `tests/test_test_page.py`, Playwright.

In the tree card only (V2), each non-root row's cluster gains a "Move to…" icon button (tooltip "Move to…", `aria-label`), opening a menu of `move_destinations(tree, path)` in tree order: allowed rows clickable (they `submit(action="move", path=path, dest=...)`), refused rows greyed with the reason as their second line (as the board drew), "where it is" greyed. Keyboard: the menu is a Quasar `q-menu` (arrow keys + Enter work).

- [ ] Tests: the menu lists the destinations in order with labels; clicking one stages the move and the tree card rebuilds with the node in its new place; a refused row is disabled and shows its reason; the plots' criteria rows draw no Move to…; the root row draws none. Playwright: open the menu with the keyboard, choose, the row moved. LOOK at 1280 and 900: the menu's width, long labels (TRANSFORM-like `cat(...)` labels middle-cut as elsewhere), the greyed reasons readable. Mutants: Move to… on a plot's criteria row; refused rows clickable. Commit "tree card: Move to…".

### Task 4: Drag and drop

**Files:** `authoring/capabilities.py` (`Panel.movable`), `authoring/shell/actions.py` (`ShellPanel.movable`), `authoring/shell/tree_card.py` (the rows' drag JS and zones), `authoring/builtin/edit_tree.py` (the handle + the drop handler), `authoring/shell/style.py`; tests `tests/test_test_page.py`, `tests/test_capability_conformance.py`, Playwright.

- **Spike first** (a scratch NiceGUI page, a real Chromium via Playwright's `drag_to` AND a manual `mouse.down/move/up` sequence): an HTML5 `draggable` handle on a row of an indented flat list with interleaved slot columns; `dragover` zones (top/middle/bottom quarter, V3) drawn without a server round trip; `drop` emitting ONE event to Python with `(src, target, zone)`; the page not scrolling away; Firefox not required. Report the spike's result before building; if HTML5 drag fails in Chromium through NiceGUI, STOP and report.
- `Panel.movable(on_drop: Callable[[str, str], None]) -> str | None` — returns the drag handle's source id to put on the handle, or None where the host offers no moving (every placement but a tree-card row). The tree card's `RowTarget` gets `drag=True`; `ShellPanel.movable` registers `on_drop(src_path, zone)` for that row. `EditTree._cluster` draws a `⋮⋮` handle (`aria-label="Drag to move"`, `draggable`) in the row's lead-side, and its `on_drop` maps `(src, zone)` to a `Dest` (before/after → `at(parent, index/index+1)`; onto → `onto(path)`) and `submit(action="move", ...)`. The shell names no capability (conformance stays green).
- On `dragstart` the page asks the Session (through the capability's `move_destinations`, computed ONCE per drag and handed to the JS as `{target_path: {zone: reason|null}}`) which zones each row allows, so `dragover` can show `not-allowed` + the reason locally (V3).

- [ ] Tests: user simulation — the drop handler's mapping (before/after/onto on a leaf, a combinator, the root) and the staged move; conformance — the shell names no capability; Playwright — drag a leaf between two rows (the order changes; plots reorder), onto a combinator (appended), onto a leaf (wrapped in `and`), onto a refused target (nothing staged, the reason shows), with a real mouse (`page.mouse` down/move/up over the zones, not only `drag_to`). LOOK at 1280 and 900 mid-drag: the accent line, the outline, the not-allowed cursor. Mutants: zone thresholds swapped; the destinations map not refreshed between two drags; a drop on a plot's criteria row accepted. Commit "tree card: drag a criterion to move it".

### Task 5: After a move

**Files:** `authoring/shell/test_page.py`, `authoring/shell/tree_card.py`; tests `tests/test_test_page.py`, Playwright.

V4: `page._close()` (editors, form rows) before the move is staged; after it, the moved node's row is revealed (scroll into view + the jump's flash) and the notes show as one line in its slot ("Moved." + notes joined by " · "), cleared by the next action or edit. The swap hazard (two leaves on one variable) gets a regression test: open the first leaf's editor, move the second above it, the editor is closed (not rebound).

- [ ] Tests: the swap hazard; the reveal (the row in view); the notes line text for k, warn removal, weight; the line clears on the next action. Mutants: editors not closed; notes line never cleared. Commit "a move closes the editors and says what it changed".

### Task 6: Record it; the gate

- [ ] `docs/decisions.md` D144 (the rulings D1-D4; V1-V5; what it enforces that the validator does not — D4 on moves only; what it reverses — nothing; the known limits), `docs/usage.md` (moving criteria), CLAUDE.md (one clause in the `dstf author` block), the roadmap (U4 BUILT).
- [ ] The controller: two executing reviews before Task 1 (archives; review a builds T1-T2, review b builds T3-T5 with the SPIKE first), a mutation review per part and per fix batch, and the TRANSFORM gate over a COPY (`/tmp/claude-1000/gate-U3` can be reused or replaced): open a test with a nested tree if one exists (else stage a nested tree in memory on a flat test — its first move materializes nothing, V1), move by menu and by drag (real mouse) on BranchingDynamicPipes, the pending count 1, the preview diff, Discard restores; plot order follows; owner files hash-identical.
