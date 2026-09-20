# The Test Page Implementation Plan (sub-project 3b)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Test page stub with the redesign's Test page (roadmap §3, Status "3b"; brief §3.2 "The Test page follows the old per-test page, modernised"; DESIGN.md "Criteria rows", "Plots are modular", "Header", "Buttons", "Status badges"): a sticky test header under the shared one; the criteria tree closed to one line that names what failed, opening to the full tree with `+`, wrap and `−` on every node; the test's plots stacked, each drawn when it scrolls into view; a tracked variable's criteria rows under its plot; a criterion's editor opening BELOW its row, one at a time, with the plot above as its editing surface; an edit redrawing only the plots it changed; Focus as a button per plot with a "Showing 1 of N plots" bar. It re-hosts what went with the old shell (roadmap §3): the plot redrawn in place after a drag, a refused drag shown in the editor and snapped back, the figure hook's per-owner clearing.

**Architecture:** Four moves. (1) **Two more ways to host a capability, still by placement only.** Today a placement is a button that opens a dialog (`ActionBar`). The Test page adds an *inline host* (`InlineHost`): every capability placed at a placement gets its `panel` built straight into a slot the page owns — the criterion editor (CRITERION) and a tree node's controls (the new `Placement.NODE`). The shell still knows no capability by name: Edit criteria and Edit on plot read as ONE editor because both are placed at CRITERION and stacked without headings; Edit tree becomes the `+` / wrap / `−` cluster because its panel at NODE *is* that cluster, and "Add a criterion for x" because its caption at VARIABLE says so. (2) **One `FigureHook` per plot card.** A card owns its `ui.plotly`, its hook and its snap-back; a panel hosted under that card subscribes through `hook.owned(owner)`, and closing or rebuilding the editor clears exactly that owner. (3) **Render on scroll is an `IntersectionObserver` and one global event.** A card is a 320 px slot until the browser says it is near the viewport; the page answers by building the plot. Server-side the entry is `PlotCard.show()`, which tests call directly. (4) **Follow by signature.** On a Session event for this model the page compares, per rendered plot, a signature of what the plot draws (the leaves on its series, their parameters and verdicts, the open leaf) and re-sends only the figures whose signature moved, through `update_figure` — `Plotly.react` on the same node, zoom kept. A change of the tree's SHAPE (paths, variables, metrics) or of the plot layout rebuilds the cards and closes the editor.

**Tech Stack:** Python 3.10+, NiceGUI 3.16 (`ui.plotly`, `ui.menu`, `ui.on`, `ui.run_javascript`, the in-process user simulation), Plotly figure dicts, pytest + pytest-asyncio (auto mode), Playwright + Chromium, uv.

**Owner rulings this plan builds on (2026-09-20, on a rendered board; not reopened):** the tree loads CLOSED to one line naming what failed (board 1A: chevron, the root's pill, `and · 4 criteria`, `3 pass · 1 fail: tube · pipe.p[1]`, "Show tree"; the line "names what failed and jumps to it"); open, it is the board's 1B drawing — a pill, `metric · variable`, the readout, then `+`, wrap, `−`, always visible; an editor opens BELOW its row at the plot's width, a long table scrolling inside it after 8 rows (2A); Focus is a button on each plot with a bar "Showing 1 of N plots: <name> — Show all N plots", the focused plot's button reading "Focused" (5A). The 40 px rail (3A) and the folded array row (4A) are 3c's. Where the board did not rule, the artboards' labels and structure hold (`docs/design/authoring-tool/pages.py`: `crit_row`, `var_section`, `tree_card`, `tube_editor`).

**Decisions this plan takes where the brief, the board and the artboards left the choice open** (each marked in its task):
1. `Placement.NODE` joins the contract, and `Selection` gains `node_path` (Task 2). A node's controls are a capability's panel drawn inline at that node; nothing else lets the shell draw `+` / wrap / `−` without knowing Edit tree by name. `leaf_path` keeps meaning "a criterion"; a combinator's path never rides in it.
2. Edit tree leaves the TEST placement (Task 2): brief §3.2 — "Edit tree becomes the +/−/wrap controls on tree nodes"; "no tab". Its old rows-and-forms panel is deleted with it. It attaches at NODE (the cluster) and at VARIABLE (the card footer's "Add a criterion for x").
3. Edit on plot moves from VARIABLE to CRITERION and is scoped to the open leaf (Task 2): its tables are that leaf's, and a drag on another leaf's handle is refused by name. Brief §3.2: "criterion (the inline editor and plot gestures)".
4. The wrap, add and edit forms open in a `ui.menu` anchored to their button (Task 2). Neither the board nor an artboard draws them; a popover keeps the row one line high. **Shown to the owner as a screenshot at the gate (Task 10) — Mind: an unrendered design question is the owner's.**
5. `+` on a LEAF adds a criterion beside it — under its parent; when that leaf is the root, the root is wrapped in `and` with the new leaf beside it, in one op (Task 2). The artboard's tooltip is "Add a criterion beside this one".
6. `−` removes at once, no confirmation (Task 2): brief §3.5 item 8 lists the remove confirmation for sub-project 4, and a staged remove is undone by Discard.
7. The tool's figure is always built `editable=True` — the Plotly `config` never changes, so opening an editor is a `Plotly.react`, not a `newPlot`, and the zoom survives — and carries rings ONLY for the open leaf (`rings_for`, Task 1). With no editor open a drag of a bound line snaps back and stages nothing. Rings for every leaf at once overlap (the spike grabbed the wrong leaf's ring on FakeLib's TREE).
8. The sticky test header is a second row INSIDE the shared `ui.header` (Task 4): Quasar's header is `position: fixed`, so a row inside it is sticky with no offset arithmetic and no dependence on the first row's height.
9. A plot's toolbar has Focus only (Task 5). The overlay picker and the error select are not rebuilt: overlays and error traces are already in the figure as `legendonly` traces, the legend toggles them, and `uirevision` keeps the choice across a redraw. "Set window" is sub-project 4's; Add series / Separate / Remove plot / Add plot are 3c's (they stage `/plots` from the drawer).
10. A finished job marks every rendered plot stale (Task 7): a rerun changes trajectories, which no leaf signature sees.

**Not built here, named so nobody looks for it:** the key-stats row (artboard `key_stats`; its numbers come from the run's statistics files, which the Session does not read yet — logged for the owner after the build); the rail, the Tests and Variables drawers, array folding, the folded criteria row, Add plot / Add series / Separate / Remove plot, the 2 px ring on the selected plot (3c); Set window, Reset, Seed / Snapshot from reference, live per-row readouts, Shift+click gestures, the remove confirmation, Esc, a panel following an edit staged elsewhere beyond what Task 7 states, the leaf's config error above its editor (sub-project 4); the running state (sub-projects 5, 6); the "when to use it" tooltips on capability buttons (no contract text for them yet).

**Review provenance.** To be filled by the executing review (Task "review" in the build loop, before Task 1): the reviewer applies Tasks 1–7 in a `git archive` copy, runs every red state and every named mutation, and the findings are folded here.

---

## Conventions for every task

- Run from the repo root with `uv run --no-sync ...` (agents share the venv). Gates before every commit, each redirected to a log whose summary line is READ, never piped through `tail`: `uv run --no-sync ruff check`, `uv run --no-sync ruff format --check` (fix with `ruff format <files>`), `uv run --no-sync mypy` **bare, no path**, `uv run --no-sync pytest -q -m "not playwright" > <scratchpad>/gate-<task>.log 2>&1; echo "exit $?"; grep -E "passed|failed|error" <scratchpad>/gate-<task>.log | tail -1`, and `uv run --no-sync pytest -q -m playwright` the same way — the browser tests RUN, they do not skip.
- **Red before green, shown.** Every test step runs the new test on the unchanged code first and the step says what fails. A test that does not fail on the unchanged code — or on the named mutation, for tests that guard rather than reproduce — is not landed until it does.
- **Named mutations.** Each task lists the mutations its tests must catch. The task's fresh-context review applies each in a `git archive` copy (`export PYTHONPATH=<archive>/src`; `uv run --no-sync --project <repo> pytest -c <archive>/pyproject.toml -o pythonpath=<archive>/tests --rootdir=<archive> -p no:cacheprovider <files>`; the reviewer prints the module's `__file__` first) and reports which survived. A surviving mutation is a plan defect: fix the test.
- **Commit with explicit paths — `git add <new files>` then `git commit -F <scratchpad>/msg-task<N>.txt -- <paths>` — never a bare `git commit`, never `--amend`, never `git stash`.** Every agent writes its message to ITS OWN file, named for its task. Messages end with the attribution trailer the session's instructions specify. Nothing is pushed.
- **Never run `dstf run` / `dstf compare` / `dstf author` against `testing_output/` or any real work directory**, and never `main([..., "author"])` in a scratch script. Task 10's gate is the orchestrator's and works over a COPY.
- **Done-checks are predicates over the end state**, never counts and never "this grep returns nothing".
- Locate code by CONTENT, not by line numbers; any quoted here are from 70aaabf.
- `docs/ideas.md` and `.soul/` are not touched. `docs/decisions.md` gains exactly one entry, D131, in Task 9. No `DSTF-NNN` id in any tracked file.
- **The UI-import predicate holds:** every file under `src/dstf/authoring/` other than `shell/*`, `forms.py` and `builtin/*` imports no UI framework; `builtin/*` imports `nicegui` only inside `panel`.
- **Markers.** NiceGUI's `.mark()` is server-side only: simulation tests find by marker, the browser test by `dstf-*` class, role and name. Every marker and class this plan introduces is listed in its task; tests and code must agree on them to the character.
- **NiceGUI facts this plan relies on** (the sub-project 2 plan's list holds; these were spiked on 2026-09-20 against the installed 3.16.0, in Chromium over the figure `Session.figure` emits for FakeLib.D with `tests/test_handles.py::TREE` staged, and under the user simulation):
  1. `ui.plotly.update_figure(fig)` reaches the browser as `Plotly.react` on the SAME node when `fig["config"]` is JSON-equal to the last one sent, and as `Plotly.newPlot` plus re-attached handlers when it is not (NiceGUI's `plotly.js`). Over the tool's figure (`uirevision: "keep"`) a zoom set before the update was still there after it.
  2. Twelve `ui.plotly` slots on one page, each with its own `FigureHook`: a ring dragged on plot 1 dispatched to plot 1's hook alone, and its `on_shape_moved` alone ran. Every plot sends one `plotly_relayout` of `{"autosize": true}` when it mounts; it reaches handlers with id `""`.
  3. Render on scroll: a page-level `IntersectionObserver` (`rootMargin: "200px"`) over `.dstf-plot-slot` elements that calls `emitEvent("dstf_plot_visible", el.dataset.plot)`, answered by `ui.on("dstf_plot_visible", handler)` — `e.args` is the string. At 1280×900, 3 of 12 slots rendered at load, 7 after a 1500 px wheel; a `scrollTo(bottom)` JUMP rendered the last three and skipped the two it never crossed, which render when reached. A warm server-side render was 5 ms.
  4. Under the user simulation a fire-and-forget `ui.run_javascript(...)` in a page build does not raise; there is no browser, so no slot ever becomes visible by itself: tests call `PlotCard.show()`. Elements inside an unopened `ui.menu` are found by `user.find(marker=...)` and their `click()` runs the handler.
  5. An off-centre grab of a ring is a pixel RESIZE (`{"x0","x1","y0","y1"}`), which `edit_on_plot._position` already treats as no move; the snap-back redraw restores the ring. A browser test that drags a ring grabs its CENTRE.

## File structure

| Path | Responsibility after this plan |
|---|---|
| `src/dstf/figures/plotly.py` | `variable_figure` / `plot_figure` / `_leaf_traces` take `rings_for: Collection[str] \| None` (None: every leaf's rings, as today). |
| `src/dstf/authoring/session.py` | `Selection.node_path`; `Session._editable_view`; `Session.figure(model, variable, *, editing=None)` and `plot_figure(model, index, *, editing=None)` — `editing` a collection of leaf paths whose rings are drawn, None meaning all; a multi-series plot drawn over the cached bases. `SessionView` mirrors both. |
| `src/dstf/authoring/capabilities.py` | `Placement.NODE`; the `Placement` and `Panel` docstrings say what each placement hosts. |
| `src/dstf/authoring/builtin/edit_tree.py` | Placements `{NODE, VARIABLE}`; the panel at NODE is the `+` / wrap / edit / unwrap / `−` cluster with its forms in menus; at VARIABLE the "Add a criterion for x" form; `act(action="add")` beside a leaf and under a root leaf. The rows panel is gone. |
| `src/dstf/authoring/builtin/edit_on_plot.py` | Placement `{CRITERION}`; scoped to `selection.leaf_path`. |
| `src/dstf/authoring/shell/actions.py` | `ActionBar(..., figure=None)` hands a plot's hook to a dialog panel; `InlineHost`: every capability at a placement, its panel inline, its error above it. |
| `src/dstf/authoring/shell/header.py` | `Header(shell, below=None)`: the first row unchanged inside `.dstf-header-row`; `below()` builds a second row, `.dstf-subheader`. |
| `src/dstf/authoring/shell/test_page.py` | `TestPage`: the sub-header, the tree card, the focus bar, the cards, the one open editor, `handle` → `sync`. |
| `src/dstf/authoring/shell/tree_card.py` | NEW. `TreeCard`: the closed line, "Show tree", the rows with NODE hosts, `summary_text`. |
| `src/dstf/authoring/shell/plot_card.py` | NEW. `PlotCard`: header, Focus, the slot and `show()`, the hook and the snap-back, criteria rows, the editor slot, the VARIABLE footer, `signature()`, `redraw()`. `CriterionRow`. |
| `src/dstf/authoring/shell/style.py` | The sub-header, the tree line, the card, the criteria row, the tinted pill, the editor's rule and indent, the table scroll, the focus bar. |
| `tests/test_figures.py`, `tests/test_plot_figure.py`, `tests/test_session.py` | `rings_for`; `editing`; the cached multi-series path. |
| `tests/test_builtin_capabilities.py`, `tests/test_edit_on_plot.py`, `tests/test_capabilities.py` | The re-placed capabilities; `Placement.NODE`. |
| `tests/test_test_page.py` | NEW. The page under the user simulation. |
| `tests/test_capability_conformance.py` | A capability the shell has never heard of attaches at NODE, VARIABLE and CRITERION. |
| `tests/test_author_test_page_playwright.py` | NEW. The page in Chromium over FakeLib. |
| `docs/…`, `DESIGN.md`, `CLAUDE.md`, the two-tier spec §4.6, the roadmap | Task 9. |

---

### Task 0: The venv and the baseline gate

**Files:** none changed.

- [ ] **Step 1:** `uv run --no-sync python -c "import nicegui, playwright.sync_api, fmpy; print(nicegui.__version__)"` prints `3.16.0`. If an import fails: `uv sync --extra dev --extra fmpy --extra om --extra author --locked --inexact`, then `uv run --no-sync playwright install chromium`.
- [ ] **Step 2:** Run the four gates of the Conventions and record the summary lines. Expected at 70aaabf: ruff, format and bare mypy clean; `2209 passed, 3 skipped` (not playwright); `29 passed` (playwright). A different baseline is recorded and is not a stop; a failing baseline IS a stop.

**Done when:** both commands produced their output in this session's log.

---

### Task 1: Rings for the open leaf only; a multi-series plot over the cached bases

Decision 7, and the first bullet of the 3a reviews' follow-ups (a multi-series `Session.plot_figure` re-assembled every series on every call).

**Files:**
- Modify: `src/dstf/figures/plotly.py` (`_leaf_traces`, `variable_figure`, `plot_figure`), `src/dstf/authoring/session.py` (`Session.figure`, `Session.plot_figure`, `SessionView.figure`, `SessionView.plot_figure`)
- Test: `tests/test_figures.py`, `tests/test_plot_figure.py`, `tests/test_session.py`

- [ ] **Step 1: Write the failing tests.**

In `tests/test_figures.py`, beside the existing ring tests (locate `"xsizemode"`), using that module's own view fixture for a view with at least two handle-bearing leaves (the fixture the existing ring test builds its `expected` from); name the two leaf paths `first` and `second` from `view.leaves[i].path`:

```python
def _ring_names(fig: dict) -> set[str]:
    return {s["name"] for s in fig["layout"]["shapes"] if s.get("type") == "circle"}


def test_rings_for_limits_the_rings_to_the_named_leaves(<the module's view fixture>):
    view = <the view the existing ring test uses>
    every = _ring_names(variable_figure(view, None, editable=True))
    first = next(lv.path for lv in view.leaves if any(n.startswith(lv.path + "/") for n in every))
    only = _ring_names(variable_figure(view, None, editable=True, rings_for=(first,)))
    assert only and only < every
    assert all(name.startswith(first + "/") for name in only)
    assert _ring_names(variable_figure(view, None, editable=True, rings_for=())) == set()


def test_rings_for_changes_nothing_but_the_rings(<fixture>):
    view = <same>
    full = variable_figure(view, None, editable=True)
    none = variable_figure(view, None, editable=True, rings_for=())
    assert none["data"] == full["data"] and none["config"] == full["config"]
    assert [s for s in none["layout"]["shapes"]] == [
        s for s in full["layout"]["shapes"] if s.get("type") != "circle"
    ]
```

In `tests/test_plot_figure.py`, beside `test_every_leaf_of_every_series_keeps_its_geometry_and_editable_rings`, the same two assertions through `plot_figure(views, None, editable=True, rings_for=...)` over that test's views.

In `tests/test_session.py`, in the class that holds `test_the_tools_figure_rings_every_marker_handle` (its `session` fixture stages a tree on D; read its setup and reuse it):

```python
    def test_editing_names_the_leaves_that_get_rings(self, session):
        <stage the same tree that test stages>
        every = {s["name"] for s in session.figure(D, "x")["layout"]["shapes"] if s["type"] == "circle"}
        leaf = "/metrics/children/0"
        some = {s["name"] for s in session.figure(D, "x", editing=(leaf,))["layout"]["shapes"] if s["type"] == "circle"}
        assert some and some < every and all(n.startswith(leaf + "/") for n in some)
        none = session.figure(D, "x", editing=())
        assert not [s for s in none["layout"]["shapes"] if s["type"] == "circle"]
        assert none["config"] == session.figure(D, "x")["config"]  # react, never newPlot
```

and in `TestPlots` (it opens `_plots_project` and stages `LAYOUT`; read `_open` there):

```python
    def test_a_multi_series_plot_is_drawn_over_the_cached_bases(self, tmp_path, monkeypatch):
        session = self._open(tmp_path)
        <stage LAYOUT on A as the neighbouring tests do>
        first = session.plot_figure(A, 0)
        import dstf.reporting.context_builder as cb
        calls = []
        real = cb.build_trajectories
        monkeypatch.setattr(cb, "build_trajectories", lambda *a, **k: calls.append(1) or real(*a, **k))
        again = session.plot_figure(A, 0)
        assert again == first
        assert calls == []  # the second draw assembles nothing

    def test_a_multi_series_plot_takes_editing(self, tmp_path):
        session = self._open(tmp_path)
        <stage LAYOUT, then a tube leaf on x: PatchOp(A, "add", "/metrics", <TREE from test_handles>)>
        fig = session.plot_figure(A, 0, editing=())
        assert not [s for s in fig["layout"]["shapes"] if s.get("type") == "circle"]
```

- [ ] **Step 2: Run them red.** `uv run --no-sync pytest -q tests/test_figures.py tests/test_plot_figure.py tests/test_session.py -k "rings_for or editing or cached_bases"`. Expected: `TypeError: ... unexpected keyword argument 'rings_for'` / `'editing'`; the cached-bases test fails with `calls == [1, 1]` (one `build_trajectories` per series).

- [ ] **Step 3: Implement.**

`figures/plotly.py`: `_leaf_traces(view, ctx, *, editable, rings_for=None)` — the ring line becomes

```python
        if editable and (rings_for is None or leaf.path in rings_for):
            rings.extend(_handle_rings(markers))
```

`variable_figure(view, max_samples=None, *, editable=False, rings_for=None)` and `plot_figure(views, max_samples=None, *, editable=False, labels=None, rings_for=None)` pass it through (the single-view branch of `plot_figure` too). Type: `Collection[str] | None`. Docstrings: "``rings_for``: the leaf paths whose handles get a draggable ring; ``None`` rings every leaf. The Test page names the open criterion, so two leaves' rings never overlap; `config` does not depend on it."

`session.py`: extract the body of `figure` after the `KeyError` guard into

```python
    def _editable_view(self, model_id: str, variable: str) -> VariableView:
        """The decimated base (cached) joined to the leaves decimated on
        their own — what both figure reads draw, WITHOUT a budget."""
```

returning `with_leaves`. Then

```python
    def figure(self, model_id, variable, *, editing: Collection[str] | None = None) -> dict:
        ...guard...
        return variable_figure(self._editable_view(model_id, variable), None, editable=True, rings_for=editing)

    def plot_figure(self, model_id, index, *, editing: Collection[str] | None = None) -> dict:
        ...
        if len(plot.series) == 1:
            return self.figure(model_id, plot.series[0], editing=editing)
        return plot_figure([self._editable_view(model_id, n) for n in plot.series], None, editable=True, rings_for=editing)
```

`SessionView.figure` / `plot_figure` forward `editing`. Keep `figure`'s docstring's cache paragraph on `_editable_view`.

- [ ] **Step 4: Green, then the whole figure and session files.** `uv run --no-sync pytest -q tests/test_figures.py tests/test_plot_figure.py tests/test_session.py tests/test_figure_goldens.py`. Every golden holds: `rings_for=None` is the old figure.

- [ ] **Step 5: Gates, commit** (`figures: rings for the named leaves only; session: a multi-series plot over the cached bases`).

**Named mutations:** (a) `leaf.path in rings_for` → `True`; (b) drop `rings_for=` in `plot_figure`'s multi-view call to `_leaf_traces`; (c) drop it in the single-view branch of `plot_figure`; (d) `Session.plot_figure` multi branch back to `self.view(...)` with the budget; (e) `Session.figure` ignoring `editing`; (f) `editable=bool(editing)` in `Session.figure` (the config test).

**Done when:** the new tests pass, the goldens are untouched, and a multi-series `plot_figure` called twice calls `build_trajectories` zero times the second time.

---

### Task 2: The contract — `Placement.NODE`, `Selection.node_path`; Edit tree as node controls; Edit on plot at the criterion

Decisions 1–6.

**Files:**
- Modify: `src/dstf/authoring/capabilities.py`, `src/dstf/authoring/session.py` (`Selection`), `src/dstf/authoring/builtin/edit_tree.py`, `src/dstf/authoring/builtin/edit_on_plot.py`
- Test: `tests/test_capabilities.py`, `tests/test_builtin_capabilities.py`, `tests/test_edit_on_plot.py`

- [ ] **Step 1: Write the failing tests.**

`tests/test_builtin_capabilities.py` — replace the placements table's rows for the two capabilities (locate `assert EditTree.placements == {T}`): Edit tree is `{Placement.NODE, Placement.VARIABLE}`, Edit on plot `{Placement.CRITERION}`; delete the Edit tree PANEL tests that drive the old rows (`wrap:{path}` buttons in a TEST-scoped panel) and keep every `act` test. Add (the module's `session` fixture and `host` import are there; `N = "/metrics/children/0"`):

```python
ROOT = "/metrics"


def _node(model, path):
    return Selection(tests=frozenset({model}), node_path=path)


def test_edit_tree_needs_one_test_and_a_node_or_a_variable(session):
    cap = EditTree()
    assert cap.available(SessionView(session, Selection(tests=frozenset({A})))) == (
        "select a tree node or a variable"
    )
    assert cap.available(SessionView(session, _node(A, ROOT))) is True
    assert cap.available(SessionView(session, Selection(tests=frozenset({A}), variable="x"))) is True


def test_the_caption_at_a_variable_names_it(session):
    view = SessionView(session, Selection(tests=frozenset({A}), variable="x"))
    assert EditTree().caption(view) == "Add a criterion for x"


def test_add_beside_a_root_leaf_wraps_the_root_in_and(session):
    # A's implicit tree is one nrmse leaf at the root.
    view = SessionView(session, _node(A, ROOT))
    assert "combinator" not in session.tree(A)
    outcome = EditTree().act(view, action="add", model_id=A, path=ROOT, variable="x", metric="range")
    (op,) = outcome.ops
    assert (op.op, op.path) == ("add", ROOT)
    assert op.value["combinator"] == "and"
    assert [c.get("metric", "nrmse") for c in op.value["children"]] == ["nrmse", "range"]
    session.apply(outcome)
    assert len(session.tree(A)["children"]) == 2


def test_add_beside_a_leaf_appends_to_its_parent(session):
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(TREE))])
    view = SessionView(session, _node(D, "/metrics/children/1"))
    outcome = EditTree().act(view, action="add", model_id=D, path="/metrics/children/1", variable="x", metric="nrmse")
    (op,) = outcome.ops
    assert (op.op, op.path) == ("add", "/metrics/children/-")


def test_add_under_a_combinator_is_unchanged(session):
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(TREE))])
    outcome = EditTree().act(SessionView(session, _node(D, ROOT)), action="add", model_id=D, path=ROOT, variable="x", metric="nrmse")
    (op,) = outcome.ops
    assert (op.op, op.path) == ("add", "/metrics/children/-")


async def test_the_node_panel_is_the_cluster_and_its_forms_stage(session):
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(TREE))])
    leaf = "/metrics/children/1"
    async with host(session, EditTree(), _node(D, leaf)) as (user, errors, _figure):
        for marker in (f"add:{leaf}", f"wrap:{leaf}", f"remove:{leaf}"):
            await user.should_see(marker=marker)
        await user.should_not_see(marker=f"edit:{leaf}")  # a leaf has no combinator to edit
        user.find(marker=f"apply-wrap:{leaf}").click()
        await _settled(lambda: "combinator" in session.tree(D)["children"][1])
        assert errors == {}


async def test_the_root_has_no_remove_and_a_combinator_has_edit(session):
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(TREE))])
    async with host(session, EditTree(), _node(D, ROOT)) as (user, _errors, _figure):
        await user.should_see(marker=f"edit:{ROOT}")
        await user.should_not_see(marker=f"remove:{ROOT}")


async def test_remove_stages_at_once(session):
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(TREE))])
    n = len(session.tree(D)["children"])
    async with host(session, EditTree(), _node(D, "/metrics/children/1")) as (user, _e, _f):
        user.find(marker="remove:/metrics/children/1").click()
        await _settled(lambda: len(session.tree(D)["children"]) == n - 1)


async def test_the_variable_panel_adds_a_criterion_for_it(session):
    sel = Selection(tests=frozenset({A}), variable="x")
    async with host(session, EditTree(), sel) as (user, errors, _figure):
        user.find(marker="apply-add:x").click()
        await _settled(lambda: "combinator" in (session.tree(A) or {}))
        assert errors == {}
```

(`TREE` from `test_handles`, `copy`, `PatchOp`, `_settled` — import or copy the helper as `tests/test_edit_on_plot.py` does.)

`tests/test_edit_on_plot.py` — `ON_D` / `ON_A` gain `leaf_path="/metrics/children/0"` (ON_A: `"/metrics"` where a test stages a root leaf — read each test's staging; `_drag` builds its selection from the handle's leaf path via `_leaf_path_of` semantics: pass `leaf_path=` explicitly per call). Replace `test_available_needs_one_test_and_a_handle_bearing_leaf` with:

```python
def test_available_needs_one_open_criterion_with_handles(session):
    _tree_on_d(session)
    cap = EditOnPlot()
    one = frozenset({D})
    assert cap.available(SessionView(session, Selection(tests=one, variable="x"))) == "open one criterion"
    assert cap.available(SessionView(session, Selection(tests=one, variable="x", leaf_path="/metrics/children/0"))) is True
    # children/3 is the scalar leaf: no handles, no table.
    assert cap.available(SessionView(session, Selection(tests=one, variable="x", leaf_path="/metrics/children/3"))) == "scalar has no on-plot handles"


def test_a_drag_on_another_leafs_handle_is_refused_by_name(session):
    _tree_on_d(session)
    view = SessionView(session, Selection(tests=frozenset({D}), variable="x", leaf_path="/metrics/children/1"))
    with pytest.raises(HandleEditError, match="/metrics/children/0/tube/upper/0 is not a handle of /metrics/children/1"):
        EditOnPlot().act(view, kind="handle", model_id=D, variable="x", handle="/metrics/children/0/tube/upper/0", shape={"xanchor": 0.5, "yanchor": 0.8})


async def test_the_panel_draws_only_the_open_leafs_tables(session):
    _tree_on_d(session)
    sel = Selection(tests=frozenset({D}), variable="x", leaf_path="/metrics/children/0")
    async with host(session, EditOnPlot(), sel) as (user, _errors, _figure):
        await user.should_see(marker="/metrics/children/0:tube_points:0:time")
        await user.should_not_see(marker="/metrics/children/2:points:0:time")
```

and adapt `test_the_panel_draws_a_table_per_list_field_and_stages_edits` to open one leaf at a time.

`tests/test_capabilities.py` — `Placement.NODE.value == "node"`, and a dummy registered at `{Placement.NODE}` is accepted.

- [ ] **Step 2: Run them red.** `uv run --no-sync pytest -q tests/test_builtin_capabilities.py tests/test_edit_on_plot.py tests/test_capabilities.py`. Expected: `AttributeError: NODE`, `TypeError: ... 'node_path'`, the placements assertions, the `available` sentences.

- [ ] **Step 3: Implement.**

`capabilities.py`: `NODE = "node"` after `CRITERION`. The `Placement` docstring gains: "SUITE, TEST and VARIABLE are a button carrying the capability's `caption`, its panel in a dialog; CRITERION and NODE are inline — the panel is built straight into the criterion's editor, or beside a tree node as that node's controls, with no button and no heading. At VARIABLE and CRITERION `Panel.figure` is the plot's hook; elsewhere a subscription raises." `Selection` (session.py) gains `node_path: str | None = None` with the docstring line "``node_path``: the tree node whose controls are being drawn (NODE) — a leaf or a combinator; ``leaf_path`` only ever names a criterion."

`edit_on_plot.py`: `placements = frozenset({Placement.CRITERION})`. `_selected` returns `(model_id, variable, leaf_path)` and needs all three, else `available` answers `"open one criterion"`; then the lock sentence as today; then, with `metric_name = dict(_leaf_paths(...)).get(leaf_path)`: `None` → `f"{leaf_path} is not a criterion on {variable}"`; `not _has_handles(metric_name)` → `f"{metric_name} has no on-plot handles"`. `panel`: the label reads `"drag a ring on the plot, or edit the table"`; the loop draws only `path == leaf_path`; the table's container (`_table`'s `ui.column`) gains the class `dstf-table-scroll`. `_act_handle`: after resolving `path`, when `s.selection.leaf_path` is set and `path != s.selection.leaf_path`: `raise HandleEditError(f"{handle} is not a handle of {s.selection.leaf_path}")`. The module docstring's first paragraph is rewritten for the placement (read every sentence that says "the centre plot", "VARIABLE", "every handle": Mind — a rename is dangerous in prose).

`edit_tree.py`: `placements = frozenset({Placement.NODE, Placement.VARIABLE})`.
- `available`: one test, else `"select exactly one test"`; then the lock sentence; then `s.tree(model_id) is None` → the existing sentence; then neither `node_path` nor `variable` → `"select a tree node or a variable"`.
- `caption`: `f"Add a criterion for {sel.variable}"` when `sel.node_path is None and sel.variable`, else `self.label`.
- `panel`: with `node_path` → `_cluster(...)`; else → `_add_form(ui, s, ui_panel, model_id, under=ROOT, variable=sel.variable, mark=f"apply-add:{sel.variable}")` drawn open (the dialog is its frame).
- `_cluster`: `node = _node_at(tree, path)`; a `ui.row().classes("dstf-node-controls items-center no-wrap gap-0")` of flat dense ROUND icon buttons, each `.props("flat dense round size=sm")` with `.tooltip(...)` and `.mark(...)`: `add` (icon `add`, tooltip "Add a criterion under this node" on a combinator, "Add a criterion beside this one" on a leaf, marker `add:{path}`) whose `ui.menu` holds the add form (variable select defaulting to the leaf's variable, metric select, a button marked `apply-add:{path}`); `wrap` (icon `data_array`, tooltip "Wrap in a combinator (and / or / k-of-n / warn / weighted)", marker `wrap:{path}`) whose menu holds `combinator_controls(None)` and a button marked `apply-wrap:{path}`; on a combinator `edit` (icon `tune`, "Change this combinator", `edit:{path}`, menu with `combinator_controls(node)` and `apply-edit:{path}`) and, with exactly one child, `unwrap` (icon `unfold_less`, `unwrap:{path}`, acts at once); unless `path == ROOT`, `remove` (icon `remove`, "Remove this criterion" / "Remove this node and everything under it", `remove:{path}`, acts at once). Each form's apply button calls `ui_panel.submit(model_id=..., action=..., path=..., **values)` and closes its menu.
- `act(action="add")`: `node = _node_at(tree, path)`; a combinator → as today; a leaf at `ROOT` → `Patch(ops=[PatchOp(model_id, "add", ROOT, {"combinator": "and", "children": [node, leaf]})])`; any other leaf → `parent = path.rsplit("/children/", 1)[0]`, op `add` at `f"{parent}/children/-"`. `tree is None` raises the existing `ValueError`.
- Delete `node_rows`, `draw`, `open_*_form`'s old shapes and `_row_text`; the module docstring is rewritten (it names `Shell.act` and "the tab" — the 2026-09-16 follow-ups asked for this sweep; sweep the same words at `capabilities.py`'s `Panel.submit` and in `tests/test_capabilities.py`).

- [ ] **Step 4: Green:** the three test files, then `tests/test_capability_conformance.py tests/test_suite_page.py tests/test_shell.py tests/test_authoring_imports.py` (the Test page stub now shows no Edit tree button — if a conformance or stub test asserts `action:edit-tree` on the Test page, it is updated to assert its absence and says why).

- [ ] **Step 5: Gates, commit** (`capabilities: a NODE placement; Edit tree is a node's controls, Edit on plot the open criterion's`).

**Named mutations:** (a) `add` beside a root leaf emitting `add /metrics/children/-` (the patch refuses: a leaf has no children — the test must fail on the op, not only on apply); (b) beside a non-root leaf using `f"{path}/children/-"`; (c) `_act_handle` without the other-leaf refusal; (d) the panel loop drawing every leaf's tables; (e) `remove` drawn at ROOT; (f) `caption` always `self.label`; (g) `available` True with neither node nor variable.

**Done when:** the listed tests pass; `registered_capabilities()` after `register_builtins()` places nothing built-in at TEST except Accept and Rerun.

---

### Task 3: Hosting — a plot's hook in a dialog panel; `InlineHost`

**Files:**
- Modify: `src/dstf/authoring/shell/actions.py`
- Test: `tests/test_test_page.py` (NEW; this task lands its fixtures and the host tests)

- [ ] **Step 1: Write the failing tests.** Create `tests/test_test_page.py` with the header of `tests/test_suite_page.py` (docstring: "The Test page (brief §3.2; roadmap §3): …"), its `_registry` fixture, `_settled`, `_one`, `_open`, and:

```python
@pytest.fixture
def shell(tmp_path):
    cfg, work = _two_var_project(tmp_path)  # A tracks x and y, FAIL; D tracks x, PASS
    s = Shell(cfg, work_dir=work)
    s.session.open(cfg, work_dir=work)
    s.load.phase = "done"
    return s


class _Probe:
    """A capability the shell has never heard of: a label, a submit button,
    and a figure subscription when the placement has a figure."""

    label = "Probe"

    def __init__(self, name, placements, *, answer=True, raises=None, subscribe=False):
        self.name, self.placements = name, frozenset(placements)
        self.answer, self.raises, self.subscribe = answer, raises, subscribe
        self.seen: list[tuple[str, dict]] = []

    def available(self, s):
        return self.answer

    def caption(self, s):
        return f"Probe {self.name}"

    def panel(self, s, ui_panel):
        ui.label(f"probe {self.name} {s.selection.leaf_path or s.selection.node_path}").mark(f"probe:{self.name}")
        if self.subscribe:
            ui_panel.figure.on("plotly_relayout", lambda h, p: self.seen.append((h, p)))
        ui.button("go", on_click=lambda: ui_panel.submit()).mark(f"go:{self.name}")

    def act(self, s, **inputs):
        if self.raises:
            raise ValueError(self.raises)
        return None


def _only(*caps):
    capabilities._reset_for_tests()
    for cap in caps:
        capabilities.register_capability(cap)


async def test_an_inline_host_builds_every_placed_panel_and_shows_an_act_error():
    ...
```

The three host tests, each building an `InlineHost` inside a bare `user_simulation(root=...)` page over the `shell` fixture (`shell.page_setup()` is not needed):

1. `test_an_inline_host_builds_every_placed_panel_without_a_button`: two probes at CRITERION, one at SUITE; `InlineHost(shell, Placement.CRITERION, lambda: Selection(tests=frozenset({A}), variable="x", leaf_path="/metrics"), figure=None, marker="editor")` → `probe:one` and `probe:two` are seen in registration order, `probe:suite` and any `action:*` marker are not.
2. `test_an_unavailable_inline_capability_says_why_in_place`: a probe with `answer="needs a baseline"` → a label marked `why:<name>` with that text, and no `probe:<name>`.
3. `test_an_act_error_shows_above_its_panel_and_a_success_clears_it`: a probe with `raises="no"`; click `go:<name>` → `error:<name>` reads `"ValueError: no"` and is visible; set `probe.raises = None`, click again → the label is hidden (read through `host.errors[name]`, since `User.find` excludes hidden elements) and the SAME `go` element is still there (`is`), i.e. the panel was not rebuilt.
4. `test_a_panel_that_raises_is_shown_in_its_slot`: `panel` raising `RuntimeError("boom")` → a `text-negative` label `"RuntimeError: boom"` in that capability's slot, the other capability's panel still built.
5. `test_clear_removes_the_hosts_handlers_from_the_hook`: `figure=FigureHook()`, a subscribing probe; `hook._dispatch("plotly_relayout", {"shapes[0].y0": 1})` reaches `probe.seen`; after `host.clear()` a second dispatch does not; after `host.build()` again it arrives ONCE per dispatch (no accumulation).
6. `test_an_action_bar_hands_its_figure_to_the_dialog_panel`: `ActionBar(shell, Placement.VARIABLE, ..., marker="variable-actions", figure=hook)` with a subscribing probe; click `action:<name>`; a dispatch on `hook` reaches the probe; opening the dialog twice does not double the delivery.

- [ ] **Step 2: Run red.** `ImportError: cannot import name 'InlineHost'`; test 6: `TypeError: ... 'figure'`.

- [ ] **Step 3: Implement** in `actions.py`:

```python
class InlineHost:
    """Every capability placed at `placement`, its panel built inline in
    this host's column — no button, no heading (brief §3.2: Edit criteria
    and Edit on plot read as one editor; a node's controls are a panel).
    An unavailable capability keeps its place and says why (brief §3.3).
    `build()` rebuilds from the selection as it is now; `clear()` empties
    the column and drops this host's figure handlers. A raising act shows
    above that capability's panel and changes nothing; the next success
    hides it in place — the panel is not rebuilt, so a field being typed in
    survives."""

    def __init__(self, shell, placement, selection_of, *, figure: FigureHook | None, marker: str, on_applied: Callable[[str], None] | None = None) -> None
    def build(self) -> None
    def clear(self) -> None
    errors: dict[str, ui.label]
```

- `owner = f"{marker}:{cap.name}"`; a panel gets `ShellPanel(submit, figure.owned(owner) if figure is not None else _NoFigure())`; `clear()` and the start of `build()` call `figure.clear(owner)` for every owner this host registered.
- Per capability: `error = ui.label("").classes("text-negative text-xs").mark(f"error:{cap.name}")`, hidden; then `available` (through `shell.placed`) — not True → `ui.label(sentence).classes("dstf-why").mark(f"why:{cap.name}")`; True → `ui.column().classes("w-full gap-2").mark(f"panel:{cap.name}")` with `cap.panel(view, panel)` inside a `try` that draws `f"{type(e).__name__}: {e}"` in `text-negative`.
- `submit`: `shell.session.apply(cap.act(view, **inputs))` in a `try`; on error set and show the label; on success hide it and call `on_applied(cap.name)`.
- The host's column: `ui.column().classes("w-full gap-2").mark(marker)`.

`ActionBar.__init__` gains `figure: FigureHook | None = None`; `open()` builds `ShellPanel(submit, self.figure.owned(f"{self.marker}:{cap.name}") if self.figure is not None else _NoFigure())` after `self.figure.clear(that owner)`; the dialog's Close also clears it. (`FigureHook` here is the shell's class from `.figure_hook`; the protocol import keeps its alias.)

- [ ] **Step 4: Green; gates; commit** (`shell: InlineHost — a placement's panels inline; an ActionBar hands a plot's hook to its dialog`).

**Named mutations:** (a) `build()` without the `figure.clear(owner)` (test 5 sees two deliveries); (b) success leaving the error visible; (c) success rebuilding the panel; (d) an unavailable capability skipped silently; (e) a raising panel propagating; (f) `ActionBar` still passing `_NoFigure()`.

**Done when:** the six tests pass and `tests/test_suite_page.py tests/test_capability_conformance.py` are green.

---

### Task 4: The page's frame — the sticky test header and the criteria tree card

Decision 8; board 1A / 1B.

**Files:**
- Create: `src/dstf/authoring/shell/tree_card.py`
- Modify: `src/dstf/authoring/shell/header.py`, `src/dstf/authoring/shell/test_page.py`, `src/dstf/authoring/shell/style.py`
- Test: `tests/test_test_page.py`, `tests/test_shell.py` (the header's row), `tests/test_author_suite_playwright.py` (only if a selector reads the header's direct children — read it first)

Markers: `back`, `model`, `status`, `meta`, `test-actions` (as today) — now inside `subheader`; `tree`, `tree:toggle`, `tree:root`, `tree:summary`, `tree:jump`, `tree:rows`, `tree-row:{path}`, `tree-pill:{path}`, `node:{path}` (the NODE host's marker).

- [ ] **Step 1: Write the failing tests** in `tests/test_test_page.py` (a helper first):

```python
def _page(shell, user) -> TestPage:
    (page,) = [p for p in shell.pages if isinstance(p, TestPage) and p.client is user.client]
    return page


async def _test_page(shell, user, model=A) -> TestPage:
    shell.mount()
    await _open(user, f"/test/{model}")
    return _page(shell, user)
```

1. `test_the_test_header_is_a_second_row_of_the_shared_header`: the elements marked `back`, `model`, `status`, `test-actions` all have an ancestor marked `subheader`, and `subheader`'s parent is the `ui.header` element that also holds `wordmark` (walk `element.parent_slot.parent`). `status` reads `FAIL` for A. `meta` reads `"Python · ref_0001"` (the backend's name, the primary reference's id; `"Python · no baseline"` for B).
2. `test_the_tree_loads_closed_to_one_line_naming_what_failed`: for A (one failing nrmse leaf at the root): `tree:root` reads `"nrmse"`… — precisely: for a single-leaf root the line's node text is `"1 criterion"`; for a combinator root `"and · 6 criteria"`. `tree:summary` reads `"0 pass · 1 fail: nrmse · x"`. `tree:rows` is NOT visible (`_page(...).tree.rows.visible is False`). For D (PASS): `"1 pass"`, and no element marked `tree:jump`.
3. `test_the_summary_names_the_first_failures_and_counts_the_rest`: stage on D a tree of four failing range leaves (`min_value: 5`) and one passing; summary `"1 pass · 4 fail: range · x, range · x, +2 more"` (at most two named); an advisory failure (a `warn` over a failing leaf, staged beside a passing primary) reads `"1 pass · 1 advisory: range · x"` and the root pill's class is the PASS one.
4. `test_show_tree_opens_the_rows_with_controls_on_every_node`: click `tree:toggle`; `tree:rows` visible; for TREE staged on D there is a `tree-row:` for `/metrics` and each child; each row has a NODE host (`node:{path}`) in which `wrap:{path}` is found; the root's has no `remove:/metrics`. The toggle's text flips `"Show tree"` → `"Hide tree"`.
5. `test_a_wrap_from_the_tree_restages_the_rows`: click `apply-wrap:/metrics/children/1`; `_settled` on a `tree-row:/metrics/children/1/children/0` existing; the tree stays OPEN.
6. `test_the_jump_opens_the_failing_criterion` — lands in Task 6 (it needs the editor); here only: `tree:jump` exists for A and is a button whose text is `"nrmse · x"`.
7. `test_a_test_with_nothing_scored_says_so`: B (no baseline) → `tree:summary` reads `"nothing scored: no baseline"`; C (no result) → `"nothing scored: no result"`; no `tree:toggle`.
8. `test_an_unknown_model_keeps_the_stub_message` (the existing behaviour, moved here if `tests/test_suite_page.py` holds it).

In `tests/test_shell.py`: the header's first row still holds `wordmark`…`stop` in order, inside an element with class `dstf-header-row`; the Suite page's header has NO `subheader`.

- [ ] **Step 2: Run red.**

- [ ] **Step 3: Implement.**

`header.py`: `Header(shell, below: Callable[[], None] | None = None)`. `ui.header().classes("dstf-header column no-wrap gap-0")`; inside, `ui.row().classes("dstf-header-row items-center no-wrap gap-3 w-full")` holding everything the header holds today, unchanged and in order; then, when `below` is given, `ui.row().classes("dstf-subheader items-center no-wrap gap-3 w-full").mark("subheader")` with `below()` called inside it. `style.py`: move the row's padding/min-height to `.dstf-header-row`; `.dstf-subheader { border-top: 1px solid var(--dstf-border); padding: 4px 0; min-height: 40px; }`.

`tree_card.py`:

```python
def leaves_of(view: dict | None) -> list[dict]: ...           # the leaf nodes, tree order
def leaf_status(leaf: dict) -> str:                            # "pass" | "fail" | "warn" | "queued"
    # passed True → pass; passed False and advisory → warn; passed False → fail; else queued
def leaf_name(leaf: dict) -> str:                              # f"{metric} · {variable}"
def leaf_readout(leaf: dict) -> str:                           # error, else " · ".join(score_display, criterion) without blanks
def summary_text(view: dict) -> tuple[str, list[dict]]:        # the line, and the failing leaves it names (≤ 2)
```

`summary_text`: `n_pass`, `n_fail` (non-advisory), `n_adv` from `leaves_of`; parts `"{n} pass"` always, `"{n} fail"` and `"{n} advisory"` when non-zero, joined by `" · "`; then, when anything failed, `": " + ", ".join(leaf_name(f) for f in named)` and `", +{k} more"` — hard failures named before advisory ones. `TreeCard(page)`: a `ui.card().classes("dstf-card dstf-tree w-full").mark("tree")`; the line: a flat dense button marked `tree:toggle` (icon `chevron_right` / `expand_more`, text "Show tree" / "Hide tree"), a tinted pill for the ROOT's verdict (`view["passed"]`), `tree:root`, `tree:summary`, and — when something failed — ONE flat button per named failure marked `tree:jump` (text `leaf_name`), `on_click` → `page.open_criterion(path)` (Task 6; until then a no-op method on the page). `tree:rows`: a column, hidden until toggled, built on first open and on `refresh()` while open; a row per node, indented `16px × depth` by inline style, marked `tree-row:{path}`: a pill (`tree-pill:{path}`), the node's text (`leaf_name` or `"{combinator} · {n} criteria"` — plus `" · k={k}"` for k-of-n), `leaf_readout` in `dstf-mono text-xs`, `ui.space()`, and an `InlineHost(shell, Placement.NODE, lambda p=path: Selection(tests=frozenset({model}), node_path=p), figure=None, marker=f"node:{path}")`. `refresh()`: re-reads `session.tree_view(model)`; repaints the line; rebuilds the rows when open. Open/closed state lives on the card and survives `refresh()`. No tree view: the line is the sentence of test 7 (no baseline: `comparisons.get(model)` is None or not `evaluated`, and a result exists; no result: `project.results.get(model)` is None or not `success`).

The tinted pill is one helper in `plot_card.py`'s future home — put it in `widgets.py` now: `pill(status: str, text: str | None = None) -> ui.label` with classes `dstf-pill dstf-pill-{status}`; `style.py`: `.dstf-pill { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .03em; padding: 1px 6px; border-radius: 3px; border: 1px solid; }` and per status in `("pass", "fail", "warn", "queued")`: `background: var(--dstf-{s}-pill); color: var(--dstf-{s}-dark); border-color: var(--dstf-{s}-border);` — read `reporting/theme.py::STATUS` for the key that plays "queued" (the Suite page's `_paint` uses `"queued"`); if `STATUS` has no `pill` part for it, use its `tint`.

`test_page.py`: `Header(self.shell, below=self._subheader)`; `_subheader` draws `ui.link("← Suite", "/")`, the model id (`text-subtitle1 dstf-mono`), the filled badge, `meta`, `ui.space()`, the TEST `ActionBar`. The unknown-model branch keeps its message in the body. The body: `ui.column().classes("dstf-test w-full q-pa-md gap-3")` with the `TreeCard`; the stub's "arrive with sub-project 3" label is deleted. `handle`: header, actions, and on `results` / `spec` / `loading` / `opened` / `job` → `self.sync()`, which (this task) repaints the badge and `tree.refresh()`.

- [ ] **Step 4: Green; the suite's browser test too** (`uv run --no-sync pytest -q -m playwright tests/test_author_suite_playwright.py`): the header change must not move it.

- [ ] **Step 5: Gates, commit** (`shell: the Test page's sticky header and the criteria tree card`).

**Named mutations:** (a) the tree built open; (b) `summary_text` naming advisory failures before hard ones; (c) naming every failure (no cap); (d) the root pill following the leaf tally instead of `view["passed"]`; (e) `refresh()` closing an open tree; (f) the sub-header built outside `ui.header`; (g) the NODE host's selection carrying `leaf_path` instead of `node_path`.

**Done when:** the eight tests pass; the Suite page's header has one row.

---

### Task 5: The plot cards — drawn on scroll, criteria rows, the footer

**Files:**
- Create: `src/dstf/authoring/shell/plot_card.py`
- Modify: `src/dstf/authoring/shell/test_page.py`, `src/dstf/authoring/shell/style.py`
- Test: `tests/test_test_page.py`

Markers: `plots` (the column), `plots:count`, `plot:{i}` (the card), `plot-title:{i}`, `plot-pill:{i}:{variable}`, `plot-missing:{i}`, `plot-slot:{i}`, `focus:{i}`, `criteria:{i}`, `criterion:{i}:{path}` (the row), `criterion-pill:{i}:{path}`, `criterion-readout:{i}:{path}`, `node:{i}:{path}` (the row's NODE host), `plot-only:{i}`, `variable-actions:{i}:{variable}`.

- [ ] **Step 1: Write the failing tests.**

1. `test_a_test_shows_one_card_per_tracked_variable`: A → `plot:0` titled `"x"`, `plot:1` titled `"y"`, `plots:count` reads `"2 plots · one per tracked variable"`; no `ui.plotly` element exists yet (`page.cards[0].plot is None`) and `plot-slot:0` has the class `dstf-plot-slot` and the prop `data-plot="0"`.
2. `test_show_builds_the_plot_once_with_no_rings`: `page.cards[0].show()` → `cards[0].plot.figure == shell.session.plot_figure(A, 0, editing=())`; a second `show()` builds nothing (`plot` is the same object); `cards[1].plot is None`.
3. `test_the_browser_event_shows_the_named_card`: `page.on_visible("1")` → `cards[1].plot is not None`; `on_visible("7")` and `on_visible("x")` are ignored without raising.
4. `test_a_layout_draws_its_plots`: over `_plots_project` with `LAYOUT` staged on A (one plot, series x and z): one card titled `"x, z"`, `plots:count` `"1 plot · from this test's plots key"`, `plot-only:0` reads `"z is plot only: it has no criteria."`, criteria rows for x only; a layout naming a variable the result lacks adds `plot-missing:0` `"Not in this result: no.such"`.
5. `test_criteria_rows_follow_the_tree`: TREE staged on D → `criteria:0` holds one row per leaf on x in tree order; row `/metrics/children/1` reads name `"range · x"`, its pill class `dstf-pill-pass`, its readout equals `leaf_readout` of that node in `session.tree_view(D)`; `wrap:/metrics/children/1` is found inside `node:0:/metrics/children/1`.
6. `test_the_card_pill_is_the_worst_of_its_variables_criteria`: A's card 0 pill `fail`; after staging `PatchOp(A, "add", "/metrics/children/0/tolerance", 0.5)` (the module's `LOOSEN` from `test_session`) and `_settled`, it is `pass` — and `cards[0]` is the same object (no rebuild).
7. `test_the_footer_adds_a_criterion_for_the_variable`: `action:edit-tree` inside `variable-actions:0:x` reads `"Add a criterion for x"`; click it, click `apply-add:x`; `_settled` on two rows under `criteria:0`.
8. `test_a_test_with_no_result_has_no_cards`: C → `plots:count` reads `"no plots: this test has no result"`.
9. `test_a_no_baseline_test_draws_its_result`: B → two cards, no criteria rows, `variable-actions` greyed with Edit tree's sentence.

- [ ] **Step 2: Run red.**

- [ ] **Step 3: Implement.**

`PlotCard(page, index, resolved)` where `resolved` is the `ResolvedPlot` of `session.plots(model)[index]` (read `discovery/plots.py` for its fields: `series`, `missing`, and how plot-only series are told from tracked ones — the tracked set is `session._tracked`'s public face; if none exists add `Session.tracked(model_id) -> list[str]` + the `SessionView` mirror and use it):
- `ui.card().classes("dstf-card dstf-plot w-full").mark(f"plot:{i}")`, `col gap 10px`.
- Header row: the title (`", ".join(series)`, `text-subtitle2`), one `pill` per TRACKED series that has criteria (`plot-pill:{i}:{var}`, status = worst of its leaves: fail > warn > pass > queued), `ui.space()`, the Focus button (Task 7 wires it; here it exists, flat dense no-caps, text "Focus", marked `focus:{i}`).
- The slot: `ui.element("div").classes("dstf-plot-slot w-full").props(f'data-plot="{i}"').mark(f"plot-slot:{i}")`; `style.py`: `.dstf-plot-slot { min-height: 320px; }`.
- `show()`: if `self.plot is None`: `self.hook = FigureHook(on_shape_moved=self.redraw)`; inside the slot `self.plot = ui.plotly(self._figure()).classes("w-full").style("height: 320px")`; `self.hook.attach(self.plot)`; `self._drawn = self.signature()`. `_figure()` = `session.plot_figure(model, i, editing=self.page.editing_for(i))` (Task 6 supplies `editing_for`; here it returns `()`), and on an exception a label `f"{type(e).__name__}: {e}"` in `text-negative` replaces the plot. **The hook is created in `__init__`, not in `show()`** — hosts subscribe before the plot exists; `attach` happens in `show()`.
- Criteria: `ui.column().classes("dstf-criteria w-full gap-0").mark(f"criteria:{i}")`; for each tracked series variable, each leaf of `tree_view` on it (tree order): a `CriterionRow`: `ui.row().classes("dstf-crit items-center no-wrap w-full")` marked `criterion:{i}:{path}` holding the pill, the name (`text-body2 text-weight-medium`), the readout (`dstf-mono text-xs`), `ui.space()`, the NODE `InlineHost` (marker `node:{i}:{path}`, selection `Selection(tests, variable=var, node_path=path)`); directly after each row an EMPTY `ui.column()` — that row's editor slot (Task 6). `CriterionRow.paint(leaf)` updates pill class, name and readout in place.
- `plot-only:{i}`: one line for the plot's untracked series (`"{a}, {b} are plot only: they have no criteria."` / `"{a} is plot only: it has no criteria."`); `plot-missing:{i}` as in test 4.
- Footer: per tracked series variable an `ActionBar(shell, Placement.VARIABLE, lambda v=var: Selection(tests=frozenset({model}), variable=v), marker=f"variable-actions:{i}:{var}", figure=self.hook)`.
- `style.py`: `.dstf-crit { min-height: 36px; padding: 4px 8px; border-top: 1px solid var(--dstf-border-hairline); cursor: pointer; }`, `.dstf-crit:hover { background: var(--dstf-surface-hover); }`, `.dstf-crit-open { background: var(--dstf-selection, var(--dstf-surface-hover)); }` — read `theme.py` for the selection-tint token's real name and use it.

`TestPage`: after the tree card, a bar (`plots:count`; texts as in the tests — `"{n} plots · one per tracked variable"` when `project.test(model).plots is None`, else `"… · from this test's plots key"`; `plural`), then `ui.column().classes("dstf-plots w-full gap-3").mark("plots")` of cards; `ui.on("dstf_plot_visible", lambda e: self.on_visible(e.args))`; `on_visible(arg)` parses an int in range, else returns; and the observer script of NiceGUI fact 3, run once after the cards are built and again after every rebuild (`_observe()`); the script observes `.dstf-plot-slot:not([data-seen])` and stamps `data-seen` so a re-run does not double-observe.

- [ ] **Step 4: Green. Step 5: Gates, commit** (`shell: plot cards drawn on scroll, criteria rows under their plot`).

**Named mutations:** (a) every card shown at build; (b) `show()` rebuilding on a second call; (c) rows for an untracked series; (d) the card pill taking the FIRST leaf's status; (e) `on_visible` raising on junk; (f) the NODE host on a row without `variable` in its selection (Edit tree's add form then defaults to the wrong variable — assert the add form's variable select value in test 5); (g) the figure built with `editing=None` (rings for every leaf).

**Done when:** the nine tests pass.

---

### Task 6: The editor — below its row, one at a time, the plot as its surface

Re-hosts (roadmap §3): the plot redrawn in place after a drag; a refused drag shown in the editor and snapped back; per-owner clearing.

**Files:**
- Modify: `src/dstf/authoring/shell/plot_card.py`, `src/dstf/authoring/shell/test_page.py`, `src/dstf/authoring/shell/tree_card.py` (the jump), `src/dstf/authoring/shell/style.py`
- Test: `tests/test_test_page.py`

Markers: `editor:{i}:{path}` (the InlineHost's), `editor-done:{i}:{path}`.

- [ ] **Step 1: Write the failing tests** (all over D with TREE staged unless said; `T0 = "/metrics/children/0"`, `T1 = "/metrics/children/1"`):

1. `test_a_click_on_a_row_opens_its_editor_below_it`: click `criterion:0:{T0}` → `editor:0:{T0}` is seen and its element's parent slot is the column that directly follows the row (assert via `page.cards[0].rows[T0].slot`); inside it, in order, `panel:edit-criteria` then `panel:edit-on-plot`, and NO element of kind `ui.tabs`; the row has the class `dstf-crit-open`.
2. `test_one_editor_at_a_time_page_wide`: on A (two cards) stage a tree with a range leaf on x and one on y; open the x row, then the y row → `page.open == (1, <y path>)`, the x row's slot is empty (`len(slot.default_slot.children) == 0`) and lost `dstf-crit-open`; clicking the open row again, or `editor-done`, closes it (`page.open is None`).
3. `test_the_open_criterion_is_the_only_one_with_rings`: `show()` card 0; open T0 → `cards[0].plot.figure == session.plot_figure(D, 0, editing=(T0,))` and the ring names all start with `T0 + "/"`; close → the figure equals `editing=()`; the `ui.plotly` element is the same object throughout, and `figure["config"]` never changed.
4. `test_opening_a_criterion_shows_its_plot`: open a row on a card never shown → `cards[0].plot is not None`.
5. `test_a_drag_stages_one_replace_and_the_plot_follows`: open T0, `show()`; `cards[0].hook._dispatch("plotly_relayout", {"shapes[N].xanchor": 0.5, "shapes[N].yanchor": 0.8})` with `N` the index in `cards[0].plot.figure["layout"]["shapes"]` of the shape named `T0 + "/tube/upper/0"` → one pending `replace` at T0 whose `tube_points[0]["upper"] == pytest.approx(0.3)`; the plot's figure now equals a fresh `plot_figure(D, 0, editing=(T0,))`; the table cell `{T0}:tube_points:0:upper` reads 0.3 (`_settled`) — the editor's panels were rebuilt after a drag.
6. `test_a_refused_drag_is_said_in_the_editor_and_the_plot_snaps_back`: open T1 (the range leaf); dispatch a move of T0's ring (its rings are not drawn, so dispatch by a shape index that resolves to a name under T0: build the payload against a figure with `editing=(T0,)` is NOT what the plot holds — instead drag T1's OWN line to a refused position: `{"shapes[N].y0": float("nan"), "shapes[N].y1": float("nan")}` for the shape named `T1 + "/range/min"`; read `tests/test_edit_on_plot.py::test_ids_that_name_no_movable_handle_are_refused` for an id and payload the editor refuses and use that) → nothing pending beyond the staged tree; `host.errors["edit-on-plot"]` is visible and names the leaf; `cards[0].plot.figure` was re-sent (wrap `update_figure` with a counter: exactly one call).
7. `test_typing_in_a_form_keeps_the_field`: open T1; set the `min value` number (find by the form's own marker — read `forms.py::render_form` for how fields are marked; if they are not, mark them `field:{name}` there and say so in the commit) to `-1.0` → pending `replace` with `min_value == -1.0`, and the SAME input element is still in the page (`is`): a panel-originated submit does not rebuild its own panel. `panel:edit-on-plot`'s children WERE rebuilt (the other panel follows).
8. `test_closing_the_editor_drops_its_handlers`: open T0, close; a shape dispatch on the hook stages nothing and raises nothing; `cards[0].hook._handlers` holds no entry whose owner starts with `editor:`.
9. `test_the_jump_opens_the_failing_criterion`: on A click `tree:jump` → `page.open == (0, "/metrics")`, the card was shown.
10. `test_a_shape_change_closes_the_editor`: open T1; click `remove:{T0}` in the ROW's node controls → the rows are rebuilt (one fewer), `page.open is None`.

- [ ] **Step 2: Run red.**

- [ ] **Step 3: Implement.**

`TestPage.open: tuple[int, str] | None`; `editing_for(i)` → `(path,)` when `open[0] == i` else `()`. `open_criterion(path, index=None)`: `index` defaults to the first card with a row for `path`; closes the current one; `card.show()`; `card.open_editor(path)`; redraws the card(s) whose `editing_for` changed. `close_editor()`.

`PlotCard.open_editor(path)`: the row gets `dstf-crit-open`; in the row's slot: `ui.column().classes("dstf-editor w-full")` holding a header row — `ui.label("Drag a ring on the plot, or edit the fields. Edits are staged as you make them; Save writes them.").classes("dstf-why")`, `ui.space()`, a flat dense "Done" button marked `editor-done:{i}:{path}` — and `self.editor = InlineHost(shell, Placement.CRITERION, lambda: Selection(tests=frozenset({model}), variable=var, leaf_path=path), figure=self.hook, marker=f"editor:{i}:{path}", on_applied=self._applied)`. `close_editor()`: `self.editor.clear()`, the slot cleared, the class removed.

`_applied(name)`: what a successful submit leaves to do — the Session has already emitted `results`, which reach the page on the next tick and redraw (Task 7); HERE: if `self.hook.dispatching` (the submit came from a drag) rebuild the whole editor (`self.editor.build()`), else rebuild every panel but `name`'s. For that `InlineHost.build(skip: str | None = None)` leaves capability `skip`'s slot and error label untouched (Task 3's class gains the parameter here, with its test: 7 above).

`redraw()` (also the hook's `on_shape_moved`): `if self.plot is not None: self.plot.update_figure(self._figure()); self._drawn = self.signature()`. A drag's order is therefore: handler → submit → stage → (`_applied` rebuilds the editor) → `finally` → `redraw()`.

Row click: `row.on("click", lambda: page.toggle_criterion(i, path))`; the NODE cluster inside the row must not toggle it: wrap the cluster in an element with `.on("click.stop", lambda: None)`… — NiceGUI spells it `.on("click", js_handler="(e) => e.stopPropagation()")`; under the user simulation `click()` on a child does not bubble, so test 10 cannot see this: the browser test (Task 8) asserts that clicking `wrap` opens its menu and NOT the editor.

`style.py`: `.dstf-editor { margin: 0 0 8px 16px; padding: 12px; border-left: 3px solid var(--dstf-accent); border-top: 1px solid var(--dstf-border-hairline); border-bottom: 1px solid var(--dstf-border-hairline); }`, `.dstf-table-scroll { max-height: 336px; overflow-y: auto; }` (8 rows of 42 px).

The jump: `TreeCard`'s button calls `page.open_criterion(path)` and then `ui.run_javascript` scrolling `.dstf-crit-open` into view (`scrollIntoView({block: "center"})`).

- [ ] **Step 4: Green. Step 5: Gates, commit** (`shell: the criterion editor below its row; the plot is its editing surface`).

**Named mutations:** (a) opening a second editor without closing the first; (b) `close_editor` without `editor.clear()` (test 8); (c) `_applied` always rebuilding everything (test 7's `is`); (d) `_applied` never rebuilding after a drag (test 5's cell); (e) `redraw` building a new `ui.plotly`; (f) `editing_for` returning the path for every card; (g) no redraw on close (rings stay); (h) the refused drag not shown.

**Done when:** the ten tests pass.

---

### Task 7: Follow — redraw by signature, rebuild on a shape change, Focus

**Files:**
- Modify: `src/dstf/authoring/shell/test_page.py`, `src/dstf/authoring/shell/plot_card.py`, `src/dstf/authoring/shell/style.py`
- Test: `tests/test_test_page.py`

Markers: `focus-bar`, `focus-bar:text`, `focus-bar:all`.

- [ ] **Step 1: Write the failing tests.** A helper counts `update_figure` calls per card (wrap the bound method after `show()`).

1. `test_an_edit_redraws_only_the_plots_it_changed`: A with range leaves staged on x and on y; show both cards; stage a `replace` of x's leaf (`session.stage` directly, then `shell.tick()` until `_settled`) → card 0 redrawn once, card 1 not at all; both cards are the same objects; x's row readout changed in place.
2. `test_an_unshown_plot_is_not_built_by_an_edit`: only card 0 shown; the same edit → `cards[1].plot is None`.
3. `test_a_shape_change_rebuilds_the_cards`: stage an `add` of a leaf on y → `page.cards[1]` is a NEW object with one more row; a card that was shown is shown again after the rebuild (`plot is not None`), so a rebuild never blanks what the user was looking at; the tree card, if open, is still open.
4. `test_a_layout_change_rebuilds_the_cards`: over `_plots_project`, stage `PatchOp(A, "add", "/plots", LAYOUT)` → one card titled `"x, z"`.
5. `test_a_finished_job_redraws_every_shown_plot`: `page.handle({"event": "job"})` → every shown card redrawn once even though no signature moved.
6. `test_a_discard_follows`: loosen A, open nothing, `session.discard()`, tick → card 0's pill back to `fail`, redrawn once.
7. `test_focus_hides_the_other_cards_and_the_bar_brings_them_back`: click `focus:0` on A → `cards[1].card.visible is False`, `focus-bar:text` reads `"Showing 1 of 2 plots: x"`, `focus:0` reads `"Focused"`; click `focus-bar:all` (text `"Show all 2 plots"`) → both visible, the bar hidden, `focus:0` reads `"Focus"`; clicking `focus:0` while focused also ends it. Focus survives an edit's redraw and ENDS on a rebuild (the indices may name other plots).
8. `test_focus_on_a_hidden_cards_criterion_shows_it`: focused on card 0, `page.open_criterion(<y's path>)` → focus ends (the editor must be visible).

- [ ] **Step 2: Run red.**

- [ ] **Step 3: Implement.**

`PlotCard.signature()`: `json.dumps([self.page.editing_for(i), [[leaf["path"], leaf.get("metric"), leaf.get("params"), leaf.get("window"), leaf.get("against"), leaf.get("passed"), leaf.get("score_display"), leaf.get("criterion"), leaf.get("error")] for leaf in <the tree_view leaves on this plot's series>]], sort_keys=True, default=str)`. `TestPage.shape()`: `(tuple((leaf["path"], leaf["variable"], leaf["metric"]) for leaf in leaves_of(tree_view)), tuple((p.series, p.missing) for p in session.plots(model)), verdict-less)`.

`TestPage.sync(force=False)`: one `tree_view` read per sync, handed to the tree card and the cards; badge; `tree.refresh(view)`; if `shape()` moved → `rebuild()`: remember which indices were shown, close the editor, end focus, clear and rebuild the cards column, `show()` the remembered indices that still exist, `_observe()`; else per card: `paint(view)` (pills, rows in place) and, when shown and (`force` or `signature() != _drawn`), `redraw()`. `handle`: `job` → `sync(force=True)`; `results` / `spec` whose `models` (when the event carries them) do not include this model → nothing; else `sync()`. Exceptions inside one card's paint are logged and do not stop the others.

Focus: `TestPage.focused: int | None`; `focus(i)` toggles; the bar is a `ui.row().classes("dstf-focus-bar items-center gap-3 w-full").mark("focus-bar")`, hidden when `focused is None`; `style.py`: the accent-tint background, 1 px border, 4 px radius, `padding: 4px 12px`.

- [ ] **Step 4: Green. Step 5: Gates, commit** (`shell: the Test page follows the Session plot by plot; Focus`).

**Named mutations:** (a) `sync` redrawing every shown card; (b) the signature without `params` (a drag that keeps the verdict and the readout draws nothing — add to test 1 a second edit that changes only a parameter whose readout text is unchanged, e.g. a tube point far from the trace); (c) the signature without `editing_for`; (d) `rebuild` not re-showing; (e) `rebuild` keeping focus; (f) `job` not forcing; (g) an event for another model syncing this page (count `tree_view` calls).

**Done when:** the eight tests pass.

---

### Task 8: Conformance, and the page in Chromium

**Files:**
- Modify: `tests/test_capability_conformance.py`
- Create: `tests/test_author_test_page_playwright.py`

- [ ] **Step 1: Conformance tests** (the module's `_Dummy` and `only`): a capability the shell has never heard of, registered ALONE at each of NODE, VARIABLE and CRITERION, attaches: at NODE its panel is found in every tree row and every criteria row of the Test page for D-with-TREE; at VARIABLE its caption is a button in the card's footer and its dialog panel receives a figure whose `on` does not raise; at CRITERION its panel is the whole editor. And `test_the_shell_names_no_capability`: no file under `src/dstf/authoring/shell/` contains any registered built-in's `name` or class name as a string or identifier (read each file's text; the built-ins' names come from `register_builtins()` + `registered_capabilities()`, so a new built-in is covered).
- [ ] **Step 2: Run red where red is possible** (the name scan is a guard: prove it by adding `"edit-tree"` to a scratch copy in an archive, not by editing the tree).
- [ ] **Step 3: The browser test**, shaped like `tests/test_author_suite_playwright.py` (reuse its `_start`, `_wait_until_serving` by import). The project: `_two_var_project` with a `test_spec.json` whose A entry carries `metrics` = an `and` of a tube on x (two control points inside the trace's span), a range on x and a range on y, and EIGHT more entries' worth of plots is not needed — instead a `plots` key on A listing `[x]`, `[y]`, and then `[x, y]` five times, so the page is taller than the viewport (seven plots). One test, in this order, each an assertion:
  1. `/test/FakeLib.A` loads; the sub-header's box is inside the header's box and still on screen after `window.scrollTo(0, 2000)`; `document.documentElement.scrollWidth <= clientWidth`.
  2. At load fewer `.js-plotly-plot` elements exist than `.dstf-plot-slot` elements, and at least one; after scrolling to the bottom in 600 px wheel steps every slot holds a plot.
  3. The tree line's text matches `r"\d+ pass · \d+ fail: "`; "Show tree" reveals rows; clicking the tree's first wrap icon opens a menu holding "apply wrap" and opens NO `.dstf-editor`.
  4. Click the `tube · x` row of plot 0: a `.dstf-editor` appears whose top is below that row's bottom and whose left edge is 16 px (±2) right of the row's; the plot now has circle shapes, all named under that leaf (`gd._fullLayout.shapes`); zoom state: set `xaxis.range` by `Plotly.relayout`, then
  5. drag the first ring's CENTRE 40 px up (coordinates from `xaxis.l2p` / `yaxis.l2p` as in the spike): the header's pending button's text changes to one naming 1 edit (read `header.py` for its wording); the ring's `yanchor` in `_fullLayout` is larger than before; `xaxis.range` is still the zoomed one; the graph div is the same node (a JS property stamped before survives).
  6. Click "Focus" on plot 0: exactly one `.dstf-plot` is visible and the bar reads "Showing 1 of 7 plots: x"; "Show all 7 plots" restores them.
  7. No `pageerror` and no console error was recorded; the spec file's bytes are unchanged (nothing was saved).
  8. Two screenshots to `tmp_path`, viewport-sized (never full-page): the top of the page with the tree open, and plot 0 with its editor open. The orchestrator READS thumbnails of both at the gate.
- [ ] **Step 4: Green** (`uv run --no-sync pytest -q -m playwright`), the whole suite, gates, commit (`tests: the Test page in Chromium; NODE / VARIABLE / CRITERION conformance`).

**Named mutations:** (a) a card's slot rendered at build (assertion 2); (b) the cluster without `stopPropagation` (assertion 3); (c) `Session.figure` with `editable=bool(editing)` (assertion 5: the zoom is lost to `newPlot`); (d) the editor slot placed above its row.

**Done when:** the playwright suite is green with the new file RUN, not skipped.

---

### Task 9: Docs, D131, the roadmap

**Files:** `docs/decisions.md` (D131), `docs/architecture.md`, `docs/extensibility.md`, `DESIGN.md`, `CLAUDE.md`, `docs/superpowers/specs/2026-09-08-two-tier-architecture-design.md` (§4.6, beside the sub-project 2 amendment), `docs/usage.md` (the `dstf author` section), the roadmap §3.

- [ ] **Step 1:** D131, in the log's own format (read D129 and D130): the Test page hosts capabilities three ways by placement — a captioned button with a dialog (SUITE, TEST, VARIABLE), an inline editor (CRITERION), a node's controls (NODE); `Selection.node_path`; Edit tree re-placed at NODE + VARIABLE, Edit on plot at CRITERION and scoped to the open leaf; one `FigureHook` per plot; the tool's figure always editable with rings for the open leaf only, so the Plotly config is constant and every redraw is a `react`; render on scroll by `IntersectionObserver` + one global event; follow by signature. Alternatives considered, each in a sentence: a `controls()` method on the contract; rings for every leaf; a CSS-sticky sub-header; redraw-all on every event.
- [ ] **Step 2:** `docs/extensibility.md`: the placement table gains NODE and says what each placement hands a panel (`selection` fields set, `figure` real or raising) and that a CRITERION panel must not draw a heading. `docs/architecture.md` and `CLAUDE.md`: the `shell/` line names `tree_card.py`, `plot_card.py` and "the Suite and Test pages" stays true; the CLAUDE.md "Running the Tool" comment for `dstf author` is rewritten for the page as built ("Open a test: the criteria tree, its plots, a criterion's editor under its row; drag its handles on the plot"). `DESIGN.md` "Criteria rows": the editor's hint line and "Done"; "Plots are modular": the toolbar as built (Focus; the legend toggles overlays and error traces) with the rest named as 3c / sub-project 4. Spec §4.6: "Amended in the redesign's sub-project 3b".
- [ ] **Step 3:** The roadmap §3 Status paragraph: 3b BUILT, with the gate's numbers (Task 10 fills them), and the acceptance predicates this plan met: "a test with N tracked variables shows N plots by default"; "the tree's +/−/wrap stage the same ops Edit tree staged"; "a criterion row opens its editor inline and the plot's handles follow". Sub-project 4's block keeps what is still its own.
- [ ] **Step 4:** Read every touched paragraph for a stale referent ("the centre plot", "the tab", "VARIABLE placement" for Edit on plot, "Edit tree's panel"). Gates (docs do not change them, run ruff for `CLAUDE.md`-adjacent nothing; the suite once), commit (`docs: the Test page; D131`).

**Done when:** `grep -rn "centre plot" src/dstf/authoring docs/extensibility.md` shows only sentences that are still true, each read.

---

### Task 10: The gate over TRANSFORM (the orchestrator's)

Over a COPY of the work dir and of the spec (memory: plan-tests-from-scratchpad; a scratch `testing.json` with absolute `source_path` and `reference_root`; `--work-dir` AFTER the subcommand). Before/after listings: the owner's spec md5, the reference dir, the TRANSFORM repo's `git status`.

What the feature DID, counted:
1. A test with ≥ 4 tracked variables (3a used `…Demonstrations.Examples.BatemanEquations_Test`): N cards for N variables; at load fewer plots than cards; after scrolling, all; time from navigation to the first plot, and the server-side `show()` time per card.
2. The tree line for a failing test (one of the five expected failures) names the failure; its jump opens that criterion.
3. A tube staged IN MEMORY on one variable (every TRANSFORM leaf is nrmse): through the page — the row's editor, mode → tube, Add row — then its ring dragged on the plot; the pending count, the ring's new position, the zoom kept; only that plot redrawn (count `update_figure` server-side via a log line at DEBUG, or count `plotly_afterplot` per graph div in the browser); then Discard, and the spec's bytes unchanged.
4. Focus on one plot of that test and back.
5. Four screenshots READ as thumbnails: the page top with the tree closed; the tree open with a wrap menu open (**decision 4 — this is the picture the owner rules on**); a plot with its tube editor open; the focus bar.
6. The Suite page's gate numbers from sub-project 2 (331 rows, the open time) re-read, to show the header change moved nothing.

**Done when:** the six are recorded in the roadmap's §3 Status, the screenshots are shown to the owner with decision 4's question, and the owner's files are byte-identical.
