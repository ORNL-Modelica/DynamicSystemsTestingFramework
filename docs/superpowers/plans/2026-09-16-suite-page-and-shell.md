# The Suite Page and the New Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the authoring tool's one-page shell with the redesign's shell and Suite page (roadmap §2; brief §3.2, §3.3, §3.6, §3.8; DESIGN.md "Header", "Buttons", "Disabled states explain themselves", "Status badges", "Tables"): a header both pages share; a test list that fills in as the Session loads; checkboxes that collect tests for batch actions and never change what is viewed; batch buttons that say their object, effect and count, greyed with the sentence that says why; capabilities attached by placement instead of a tab strip; selection kept per browser tab; the backend switched in place or opened in a second process — with the old `authoring/app.py` deleted and the Test page a stub route until sub-project 3.

**Architecture:** Three structural moves, each made where the state lives. (1) **Selection leaves the Session.** It is a page's state, one set per browser tab (`app.storage.tab`), and a capability reads it through the `SessionView` the page builds for it — `SessionView(session, selection)`; the Session's `select`, `selection` and the `selection` event are deleted. (2) **`Scope` becomes `Placement`** — `SUITE` / `TEST` / `VARIABLE` / `CRITERION` — and a capability declares the frozenset it attaches at plus `caption(view)`, the sentence its button carries; the shell draws one outlined button per capability at each placement and hosts its panel in a dialog. It still knows no capability by name. (3) **`Session.open` runs on a thread the shell owns**; every Session event crosses to the UI loop through one queue an app-level timer drains and fans out to every live page, so the page appears before a result is read and rows fill in per `loading` event.

**Tech Stack:** Python 3.10+, NiceGUI 3.16 (`[author]`: `ui.page`, `app.storage.tab`, `app.timer`, `ui.dialog`, the in-process user simulation), pytest + pytest-asyncio (auto mode), Playwright + Chromium, uv.

**Decisions this plan takes where the brief and roadmap left the choice open** (each marked in its task):
1. Selection is per page and per tab, not in the Session (Task 3). It is the only structure under which two tabs keep separate checked sets, and the Test page (sub-project 3) needs the same for its variable and criterion. Recorded as D129 (Task 10).
2. `caption(view) -> str` joins the capability contract (Task 4). The shell knows no capability by name, so the sentence "Rerun 2 selected tests (Dymola)" can come only from the capability; `label` stays as the panel's heading.
3. `Panel.figure` at a placement with no plot (SUITE, TEST) is an object whose `on` raises (Task 7): a subscription there is a contract misuse, and should be loud rather than silently dropped.
4. A placement's panel lives in a dialog the caption button opens (Task 7): Accept's and Rerun's panels are confirm lists, and a confirm list belongs to the moment of the click. The dialog is not rebuilt while open; a successful act closes it, a raising one shows the error in it.
5. A model whose scoring raised is announced with `error=True`; `open` emits `done` and starts the rescan even when one model's scoring raised, then propagates the failure (Task 1). Both `docs/ideas.md` entries of 2026-09-16 asked for this; a shell waiting for `done` would otherwise wait forever over a project that is in fact open and complete.
6. A backend switch and "Open in another backend…" pass the CLI's `--work-dir` through when one was given and use the default otherwise (Task 8): `--work-dir` means "this work dir"; the default work dir is already partitioned by backend.
7. The child process a spawn starts is terminated when the parent stops (Task 8): the user started one tool, and an orphan bound to a port after Stop server is a surprise.
8. A `dstf author` whose open fails serves a page saying so, with the CLI hint (Task 5): with a threaded open the CLI cannot exit first. `cmd_author`'s pre-checks — no `--config`, a dropped global flag, the missing extra — still exit as before, so `tests/test_cli.py` is unchanged but for module names.
9. Loading state is the shell's, read from the events (Task 5): the Session emits `loading` per phase and the shell keeps `LoadState` from them, so a page opened mid-load draws the right line without the Session growing a flag.

**Owner calls this plan takes, marked for the owner:**
1. **The tests of old-shell behaviours the Test page re-hosts are deleted, not held as text.** Panels following a rescore, the leaf's config error above the editor, an error cleared in place keeping the typed-in field, the active tab kept, the plot redrawn in place after a drag: their shape depends on the Test page sub-project 3 designs, so this plan names them in the roadmap's sub-project 3 and 4 blocks (Task 10) instead of carrying test text that would not fit the new page. Mind 12 asks for text when the WORK is held; here the work is re-specified downstream.
2. **VARIABLE and CRITERION placements are drawn by nobody until sub-project 3.** The roadmap's acceptance predicate "a capability the shell has never heard of attaches at each of the four placements" is met here through the shell's `placed()` query for all four and the two pages that exist (SUITE on the Suite page, TEST on the Test page stub). Task 10 states the amended predicate in the roadmap block.
3. **Between Task 3 and Task 5 the tool has no shell** (`dstf author` fails to import `run_app` and says so; `cmd_author`'s own pre-checks still run first — Task 3 moves them above the import). The build runs the tasks in sequence in one branch and nothing is pushed, so the window is never a state anyone else sees.
4. **Not built here, named so nobody looks for it:** the one-line "when to use it" tooltip per action button (brief §3.3; the contract has no text for it — sub-project 4 or 5 adds it with the queue's captions); sortable columns and a sticky header on the test list (DESIGN.md "Tables"; the list is in project order, grouped or flat — sorting has no asker yet); `Session.close` stays as the library's counterpart to `open` although no page calls it any more.

**Review provenance.** The plan text had a fresh-context review on 2026-09-16 (ACCEPT-WITH-FIXES: 6 HIGH, 9 MED, 6 LOW); every HIGH and MED and the code-bearing LOWs are folded below. A second review over the folds (ACCEPT-WITH-FIXES: 2 HIGH, 3 MED, 3 LOW) found the header reading `Config.backends` a task before it existed and an early `return` defeating the runs card's `spec` follow; both folded, `Config.backends` now lands in Task 5. A third review over those folds (ACCEPT-WITH-FIXES) spiked two more: `user.open` returns before a page that awaited its handshake resumes (every test now opens through `_open`), and the shell's first tick replays the fixture's queued open while the page waits (the greyed-while-loading test drains the queue first). Two of its spikes changed test text throughout: NiceGUI's `User.find` RAISES when nothing matches and excludes hidden elements, so absence is asserted with `should_not_see` or `pytest.raises(AssertionError)` and a hidden element is read through the page object; and a navigated-away page's client is NOT deleted under the simulation, so a test picks the page whose `client is user.client`.

---

## Conventions for every task

- Run from the repo root with `uv run --no-sync ...` (agents share the venv). Gates before every commit, each redirected to a log whose summary line is READ, never piped through `tail` (a pipe returns the tail's exit code): `uv run --no-sync ruff check`, `uv run --no-sync ruff format --check` (fix with `ruff format <files>`), `uv run --no-sync mypy` **bare, no path**, `uv run --no-sync pytest -q -m "not playwright" > <scratchpad>/gate.log 2>&1; echo "exit $?"; grep -E "passed|failed|error" <scratchpad>/gate.log | tail -1`, and `uv run --no-sync pytest -q -m playwright` the same way — the browser tests RUN, they do not skip.
- **Red before green, shown.** Every test step runs the new test on the unchanged code first and the step says what fails and where. A test that does not fail on the unchanged code — or on the named mutation, for tests that guard against a regression rather than reproduce a defect — is not landed until it does. The red states of this plan were executed before its review from a scratchpad copy of the tree (memory: plan-tests-from-scratchpad); the "Expected" lines below are what ran.
- **Named mutations.** Each task lists the mutations its tests must catch. The task's fresh-context review applies each one in a `git archive` copy (absolute `PYTHONPATH` EXPORTED, since subprocess-spawning tests inherit it; the reviewer prints the module's `__file__` first) and reports which survived. A surviving mutation is a plan defect: fix the test, not the mutation list.
- **Commit with explicit paths — `git commit -F <msgfile> -- <paths>` — never a bare `git commit`, never `--amend`.** New files need `git add <file>` first; deleted files `git rm`. Messages end with the attribution trailer the session's instructions specify. Nothing is pushed (the push waits for the whole redesign; owner call 2026-09-16).
- **Never run `dstf run` / `dstf compare` / `dstf author` against `testing_output/` or any real work directory** from a task or a review. Task 11's real-library gate is the orchestrator's and works over a COPY. Tests use `tmp_path`; the autouse `_scan_cache_home` fixture in `tests/conftest.py` redirects `XDG_CACHE_HOME`. A shell that exports `XDG_CACHE_HOME` for a script also moves Playwright's browser lookup — set `PLAYWRIGHT_BROWSERS_PATH=$HOME/.cache/ms-playwright` in that shell.
- **Done-checks are predicates over the end state**, never counts and never "this grep returns nothing".
- Implementers run sequentially in the main tree (the editable `.pth` imports the main tree, so a worktree does not isolate them); reviewers run on archives, at most two in parallel, never alongside a full suite (memory); at most ONE gate driver per command.
- `docs/ideas.md` and `.soul/` are not touched. `docs/decisions.md` gains exactly one entry, D129, in Task 10.
- **The UI-import predicate, amended:** every file under `src/dstf/authoring/` other than `shell/*`, `forms.py` and `builtin/*` imports no UI framework. Task 3 lands the test for it.
- **NiceGUI facts this plan relies on** (verified against the installed 3.16.0 on 2026-09-16): `app.timer(interval, callback)` is an app-level timer, created once the event loop runs (NiceGUI's own `App.start` uses it, so it runs under the user simulation's lifespan too); `app.storage.tab` needs `await ui.context.client.connected()` first and is created on the socket handshake, which the simulation's `user.open` performs with a `tab_id` of its own; `ui.page` decorates free functions only (closures inside `Shell.mount` here); `nicegui_reset_globals` inside `user_simulation` removes every route, so routes are registered inside the simulation (`Shell.mount`, called from `index`); `Client.is_deleted` is a property; `ui.navigate.to(..., new_tab=True)` may be blocked by the browser, which is why the spawned child opens its own tab (Task 8). Five of these — a `@ui.page` registered at runtime inside the root build, `app.storage.tab` shared across a navigation after `await connected()`, `app.timer` fanning a worker thread's event to a label with no client context, a `ui.link` click navigating, a `ui.checkbox` click toggling and firing `on_change` — were spiked under the user simulation on 2026-09-16 and held. Two more, found by the plan's reviews: `User.find` raises on no match and excludes hidden elements; and `await user.open(path)` returns as soon as the handshake is sent, BEFORE a page that awaited `client.connected()` has resumed — one loop iteration later — so every test opens a page through `_open(user)`, which waits for the header's wordmark (the spike lived in the session scratchpad; its shape is Task 6's navigation test and Task 5's progressive-open test).

## File structure

| Path | Responsibility after this plan |
|---|---|
| `src/dstf/authoring/session.py` | `Selection` stays (a page's state, handed in through the view); `Session.select`, `Session.selection` and the `selection` event are gone; `SessionView(session, selection)`; `open` emits `done` in a `finally` and starts the rescan there; `loading(results)` carries `error`; failures counted per model. |
| `src/dstf/authoring/capabilities.py` | `Placement` (SUITE / TEST / VARIABLE / CRITERION) replaces `Scope`; `Capability.placements: frozenset[Placement]` and `caption(s) -> str`; `plural()`; `register_capability` checks both. |
| `src/dstf/authoring/builtin/{accept,rerun,edit_criteria,edit_tree,edit_on_plot}.py` | Each declares `placements` and `caption`; Accept and Rerun refuse an empty selection with "… needs a selection"; their panel button carries the caption. |
| `src/dstf/authoring/shell/__init__.py` | The NiceGUI tier: exports `run_app`. The only package under `authoring/` (with `forms.py`, `builtin/*`) that imports a UI framework. |
| `src/dstf/authoring/shell/app.py` | `run_app`: the Shell, `mount`, the open on startup, `ui.run`. |
| `src/dstf/authoring/shell/shell.py` | `Shell`: the Session, the posted queue and the event queue, the app timer's `tick`, `LoadState`, the live pages, `open_async` / `_open_worker`, `placed`, `view_for`, `visible_tests`, `stop_reasons`, `busy_reason`, `spawn`, `terminate_children`, `mount`, `index`, `page_setup`. |
| `src/dstf/authoring/shell/style.py` | `STYLE`: the shell's stylesheet on top of `css_variables()` — the font token on the body and Quasar's controls, the header, rows, badges, why-lines. |
| `src/dstf/authoring/shell/widgets.py` | `Action`: a button whose label is its caption and whose disabled state carries a sentence beside it. |
| `src/dstf/authoring/shell/figure_hook.py` | `FigureHook` and `_OwnedFigureHook`, moved from the old `app.py` unchanged (sub-project 3's plot is their next client). |
| `src/dstf/authoring/shell/header.py` | `Header` (wordmark, library, backend select with its why-line, "Open in another backend…", the run indicator, the pending button, Save / Discard / Export as `Action`s, the dashboard link, Stop server with its confirm) and `PendingDrawer`. |
| `src/dstf/authoring/shell/runs.py` | `RunsCard`: the running job's progress rows from `status.json`, the last job's outcome, the notices, the artifacts — the old footer's content as a Suite card; sub-project 5 replaces it with the queue panel. |
| `src/dstf/authoring/shell/actions.py` | `ActionBar`: one `Action` per capability placed here; the dialog hosting a panel; `ShellPanel`; `_NoFigure`. |
| `src/dstf/authoring/shell/tabstate.py` | `TabState` in `app.storage.tab`: the checked set, the filter, the layout toggles. |
| `src/dstf/authoring/shell/suite.py` | `SuitePage`: progress line, CLI hint, toolbar (filter, grouped/flat, short/full, status chips, check-shown / uncheck), the `ActionBar` at SUITE, the rows, the `RunsCard`; `handle(event)`. |
| `src/dstf/authoring/shell/test_page.py` | `TestPage` stub: header, "← Suite", the model id and its badge, the `ActionBar` at TEST, the sub-project 3 note. |
| `src/dstf/authoring/app.py` | DELETED (Task 3). |
| `src/dstf/cli/author_cmd.py` | Imports `run_app` from `..authoring.shell`. |
| `src/dstf/config.py` | `Config.backends: dict[str, list[str]]` — the `backends` map of testing.json, the candidates for the backend select (Task 5). |
| `tests/test_session.py` | Three loading tests (Task 1). |
| `tests/test_figure_hook.py` | The 18 pure `FigureHook` tests, moved from the old `tests/test_shell.py`. |
| `tests/capability_host.py` | `host(session, cap, selection)`: a capability's panel alone in the user simulation. |
| `tests/test_builtin_capabilities.py`, `tests/test_edit_on_plot.py`, `tests/test_accept_and_rerun.py` | Driven through `host` / `SessionView(session, selection)` instead of the old shell. |
| `tests/test_authoring_imports.py` | The UI-import predicate, amended. |
| `tests/test_capabilities.py` | `placements`, `caption`, the registration checks. |
| `tests/test_config.py` | `Config.backends`. |
| `tests/test_shell.py` | REWRITTEN: the process, the header, Save/Discard/Export, Stop server, the pending drawer, the dashboard route, the progressive open, the runs card (ported). |
| `tests/test_suite_page.py` | The list, the toggles, the filter, the chips, click vs check, per-tab state across navigation, rows filling in, the Test page stub. |
| `tests/test_capability_conformance.py` | REWRITTEN for placements and the dialog. |
| `tests/test_author_smoke_playwright.py` | DELETED (Task 3); `tests/test_author_suite_playwright.py` is the browser test (Task 9). |
| `tests/test_cli.py` | The cached-module names in the no-extra test. |
| `docs/superpowers/specs/2026-09-08-two-tier-architecture-design.md` | §4.6 "Amended in the redesign's sub-project 2". |
| `docs/extensibility.md`, `docs/architecture.md`, `CLAUDE.md` | The contract paragraph; the tree; the project-structure line. |
| `docs/decisions.md` | D129. |
| `docs/superpowers/plans/2026-09-15-authoring-tool-redesign-roadmap.md` | Sub-project 2's status block with the gate numbers; the deleted behaviours named in §3 and §4. |

---

### Task 0: The venv and the baseline gate

**Files:** none changed.

- [ ] **Step 1: Confirm the venv carries every extra and Playwright.** `uv run --no-sync python -c "import nicegui, playwright.sync_api, fmpy; print(nicegui.__version__)"` prints `3.16.0`. If any import fails: `uv sync --extra dev --extra fmpy --extra om --extra author --locked --inexact` then `uv run --no-sync playwright install chromium`.

- [ ] **Step 2: The baseline gate, read from the log.** Run the four gates of the Conventions and record the summary lines. Expected at the plan's HEAD (9fd5332): ruff and format clean, bare mypy clean, `2088 passed, 3 skipped` (not playwright), `30 passed` (playwright, no skips). A different baseline is not a stop — record it in the handoff and go on; a failing baseline IS a stop.

**Done when:** the two commands above have produced their expected output in this session's log.

---

### Task 1: The loading events say which model failed, and `open` finishes even so

Decision 5. Three facts, each with its test: a model whose scoring raised is announced with `error=True` and every other with `error=False`; `open` emits `loading(phase="done")` and starts the rescan (or sets `rescan_done`) in a `finally`, then lets the failure propagate; `failures` counts models, not `_score` calls.

**Files:**
- Modify: `src/dstf/authoring/session.py` (`open`, lines 283–296; `_reload_results`, lines 801–863)
- Test: `tests/test_session.py` (class `TestOpen`, after `test_one_models_scoring_failure_leaves_the_results_complete_and_is_raised`)

- [ ] **Step 1: Write the three failing tests** into `TestOpen`, directly after `test_one_models_scoring_failure_leaves_the_results_complete_and_is_raised`:

```python
    def test_a_failed_models_announcement_carries_the_error_flag(
        self, session, monkeypatch
    ):
        """A per-event row filler reads `comparisons[model]` for the announced
        model; a model whose scoring raised has none there, and its event
        must say so: `error=True` on that announcement, `False` on every
        other (docs/ideas.md 2026-09-16, second entry)."""
        import dstf.authoring.session as session_mod

        real = session_mod.compare_one

        def failing(test, *args, **kwargs):
            if test.model_id == D:
                raise ValueError("boom")
            return real(test, *args, **kwargs)

        monkeypatch.setattr(session_mod, "compare_one", failing)
        events = []
        session.on("loading", events.append)
        with pytest.raises(RuntimeError):
            session._reload_results()
        flags = {e["model"]: e["error"] for e in events if e.get("phase") == "results"}
        assert flags == {A: False, B: False, C: False, D: True, G: False}

    def test_open_emits_done_and_starts_the_rescan_when_one_models_scoring_raised(
        self, tmp_path, monkeypatch
    ):
        """After a partial scoring failure the project is open and its
        results complete (the test above); a shell waiting for `done` — or
        for `rescan_done` — must not wait forever. The failure still reaches
        the caller, after `done`."""
        import dstf.authoring.session as session_mod

        real = session_mod.compare_one

        def failing(test, *args, **kwargs):
            if test.model_id == D:
                raise ValueError("boom")
            return real(test, *args, **kwargs)

        monkeypatch.setattr(session_mod, "compare_one", failing)
        cfg, work = _project(tmp_path)
        s = Session()
        events = []
        s.on("loading", events.append)
        with pytest.raises(RuntimeError, match="scoring FakeLib.D failed"):
            s.open(cfg, work_dir=work)
        assert events[-1]["phase"] == "done"
        assert s.rescan_done.wait(5)  # a Python project scans nothing: set at once
        assert s.project is not None and {A, B, C, D, G} <= set(s.project.results)
        assert D not in s.comparisons

    def test_a_model_under_two_manifest_keys_is_one_failure(self, session, monkeypatch):
        """`failures` counts models, not `_score` calls: a manifest listing one
        model under two keys (hand-edited; the tool never writes one) read
        "2 of 5 models" for one failing model (docs/ideas.md 2026-09-16)."""
        import dstf.authoring.session as session_mod

        manifest = session.project.config.work_dir / "batch_manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["test_0009"] = dict(data["test_0004"])  # D, a second time
        manifest.write_text(json.dumps(data), encoding="utf-8")
        real = session_mod.compare_one

        def failing(test, *args, **kwargs):
            if test.model_id == D:
                raise ValueError("boom")
            return real(test, *args, **kwargs)

        monkeypatch.setattr(session_mod, "compare_one", failing)
        with pytest.raises(RuntimeError, match=r"\(1 of 5 models\)"):
            session._reload_results()
```

- [ ] **Step 2: Run them red.** `uv run --no-sync pytest -q tests/test_session.py -k "error_flag or emits_done_and_starts or two_manifest_keys"`. Expected: 3 failed — the first with `KeyError: 'error'`; the second at `assert events[-1]["phase"] == "done"` (the last event is a `results` announcement; `done` never fired); the third with `AssertionError: Regex pattern did not match` (the message reads `2 of 5 models`).

- [ ] **Step 3: Change `open`.** Replace lines 283–296 (from `self._emit("spec", ...)` through the `else: done_event.set()`) with:

```python
        self._emit("spec", models={t.model_id for t in tests})
        done_event = threading.Event()
        self.rescan_done = done_event
        try:
            self._reload_results()
        finally:
            # Whether or not one model's scoring raised: the project is open
            # and its results complete (`_reload_results`), so `done` fires
            # and the rescan starts — a shell waiting on either must not wait
            # forever. The failure still propagates to the caller, after both.
            self._emit("loading", phase="done", seconds=time.monotonic() - started)
            if cached is not None:
                threading.Thread(
                    target=self._rescan,
                    args=(self.project, cached, done_event),
                    name="dstf-rescan",
                    daemon=True,
                ).start()
            else:
                done_event.set()
```

- [ ] **Step 4: Change `_reload_results`.** Its docstring's last sentence becomes: `…and the first failure is raised after the batch event — ``_finish_job`` records it as the job's error (no rows shown for a failed job); ``open`` propagates it after its ``done`` event. Each announcement carries ``error``: True for a model whose scoring raised (its verdict is absent), False otherwise.` Then replace the `announced` / `failures` block and `score_and_announce` (lines 821–839) with:

```python
        announced: set[str] = set()
        failures: dict[str, Exception] = {}

        def score_and_announce(model_id: str) -> None:
            failed = False
            try:
                self._score(model_id)
            except Exception as e:
                self.comparisons.pop(model_id, None)
                failures.setdefault(model_id, e)
                failed = True
            if model_id in announced:
                return  # one model under two manifest keys is read twice
            announced.add(model_id)
            self._emit(
                "loading",
                phase="results",
                model=model_id,
                done=len(announced),
                total=len(to_read),
                error=failed,
            )
```

and the raise at the end (lines 859–863) with:

```python
        if failures:
            model_id, error = next(iter(failures.items()))
            raise RuntimeError(
                f"scoring {model_id} failed ({len(failures)} of {len(to_read)} models)"
            ) from error
```

- [ ] **Step 5: Run green.** `uv run --no-sync pytest -q tests/test_session.py`. Expected: every test passes (the existing `test_one_models_scoring_failure_leaves_the_results_complete_and_is_raised` still passes: its assertions are unchanged).

- [ ] **Step 6: Gates, then commit** `src/dstf/authoring/session.py tests/test_session.py` with message `session: a failed model's announcement carries error=True; open emits done and starts the rescan even when one model's scoring raised`.

**Named mutations (the review applies each; every one must be caught):**
- M1.1: drop `error=failed` from the announcement → test 1 fails with `KeyError`.
- M1.2: move the `done` emit and the rescan start back after `_reload_results()` outside the `finally` → test 2 fails at `events[-1]["phase"]`.
- M1.3: `failures` back to a list appended per call → test 3 fails on `2 of 5`.

**Done when:** the three tests pass on the tree and each mutation above fails its named test on an archive; `session.py`'s `_reload_results` docstring says `error` is on every announcement.

---

### Task 2: `FigureHook` moves to the shell package

A pure move. The hook is the plot's, and the plot returns with sub-project 3; it leaves the old `app.py` before that file is deleted, with its 18 tests, so the deletion in Task 3 removes only the old shell.

**Files:**
- Create: `src/dstf/authoring/shell/__init__.py`, `src/dstf/authoring/shell/figure_hook.py`
- Modify: `src/dstf/authoring/app.py` (imports the hook from the new module; the moved code removed)
- Create: `tests/test_figure_hook.py`; Modify: `tests/test_shell.py` (the moved tests removed)

- [ ] **Step 1: Create the package and the module.** `src/dstf/authoring/shell/__init__.py`:

```python
"""The NiceGUI tier of the authoring tool (roadmap §2): the shell as a
process, the Suite and Test pages, the header, the capability placements.
The only package under ``authoring/`` — with ``forms.py`` and the built-in
panels — that imports a UI framework; the Session and the contract never
do. ``run_app`` arrives with the shell (this file exports it then)."""
```

`src/dstf/authoring/shell/figure_hook.py`: the module docstring below, then lines 64–228 of `src/dstf/authoring/app.py` (from the `# The index is bounded…` comment through the end of `_OwnedFigureHook`) verbatim, plus the imports they need (`re`, `Callable`, `Any`, `from nicegui import ui`):

```python
"""The centre plot's event hook (two-tier design §4.6 "Panel", amended in
sub-project 6): ``panel.figure.on(kind, handler)``. Moved here unchanged from
the old shell; sub-project 3's Test page attaches it to each plot."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from nicegui import ui
```

- [ ] **Step 2: Point the old shell at it.** In `app.py` delete the moved lines and the now-unused `import re`; add `from .shell.figure_hook import FigureHook` next to the other relative imports. `ShellPanel` keeps `figure: FigureHookProtocol`; nothing else changes.

- [ ] **Step 3: Move the tests.** Create `tests/test_figure_hook.py` with the module docstring `"""The figure hook's dispatch contract (§4.6, amended in sub-project 6): pure tests, no page. Moved from tests/test_shell.py with the hook."""`, the imports `import pytest` and `from dstf.authoring.shell.figure_hook import FigureHook`, and these 18 tests cut verbatim from `tests/test_shell.py` — every inline `from dstf.authoring.app import FigureHook` removed, since the module imports it once:

`test_the_figure_hook_names_the_primitive_from_meta_id`, `test_an_x_unified_click_names_the_handle_not_the_first_trace`, `test_a_shape_relayout_is_dispatched_per_shape_with_the_rendered_name`, `test_a_zoom_relayout_is_dispatched_once_raw_and_moves_no_shape`, `test_a_relayout_mixing_zoom_and_shape_keys_is_a_shape_relayout`, `test_the_shape_moved_callback_runs_when_a_handler_raises`, `test_nothing_malformed_makes_the_hook_dispatch_raise`, `test_the_figure_hook_clears_handlers_by_owner`, `test_the_figure_hook_dispatches_from_a_snapshot`, `test_a_handler_removed_during_dispatch_is_skipped`, `test_each_shape_of_a_relayout_is_dispatched_from_its_own_snapshot`, `test_the_figure_hook_is_dispatching_only_while_handlers_run`, `test_clearing_another_owner_mid_dispatch_does_not_skip_a_live_handler`, `test_a_handler_cleared_and_re_registered_mid_dispatch_is_skipped_by_identity`, `test_a_bound_method_cleared_and_re_registered_mid_dispatch_is_skipped`, `test_a_raising_handler_ends_the_relayout`, `test_a_nested_dispatch_restores_the_outer_dispatching_flag`, `test_a_shape_key_with_a_trailing_newline_or_a_non_ascii_digit_is_not_a_shape`.

The helpers they use move with them, verbatim: `_StandInPlot` (old line 663, "What `FigureHook.attach` needs of a `ui.plotly`…"), `_recording_hook` (675), `_SHAPES` (725) and `_reregistering_hook` (1031) — twelve of the eighteen tests call one of them — each stripped of its inline `from dstf.authoring.app import FigureHook`. Any test in the list that uses a name from `tests/test_shell.py`'s header imports (`ui`, `pytest`) keeps working through this file's imports.

- [ ] **Step 4: Run.** `uv run --no-sync pytest -q tests/test_figure_hook.py tests/test_shell.py`. Expected: `tests/test_figure_hook.py` collects 18 and passes 18; `tests/test_shell.py` passes with 18 fewer tests than before; `uv run --no-sync python -c "from dstf.authoring.shell.figure_hook import FigureHook; from dstf.authoring import app; assert app.FigureHook is FigureHook"` prints nothing.

- [ ] **Step 5: Gates, then commit** — `git add src/dstf/authoring/shell/__init__.py src/dstf/authoring/shell/figure_hook.py tests/test_figure_hook.py` first — `src/dstf/authoring/shell/__init__.py src/dstf/authoring/shell/figure_hook.py src/dstf/authoring/app.py tests/test_figure_hook.py tests/test_shell.py` with message `shell: FigureHook moves to authoring/shell/figure_hook.py with its tests (pure move)`.

**Named mutations:** none — a move. The review checks the moved text against `git show 9fd5332:src/dstf/authoring/app.py` and `git show 9fd5332:tests/test_shell.py` (`diff` of the extracted ranges) and reports any line that is not a verbatim copy other than the imports.

**Done when:** the import identity check passes; `uv run --no-sync pytest --collect-only -q tests/test_figure_hook.py` lists every one of the 18 names above and no other, and the file passes; `tests/test_shell.py` collects none of them; the names `tests/test_shell.py` and `tests/test_figure_hook.py` collect together (`--collect-only -q`) are the set `tests/test_shell.py` collected at 9fd5332 (`git stash` is not needed: `git show 9fd5332:tests/test_shell.py > <scratchpad>/old.py` and grep its `def test_` names).

---

### Task 3: Selection leaves the Session; the old shell and its tests go; the capability host

Decision 1 (D129). `Selection` stays in `session.py` as the frozen dataclass a page hands to `SessionView`; the Session no longer holds one. The old shell is the only code that wrote it, and it goes here with the tests that drove it; the tests of the built-in capabilities' PANELS — which still exist and return on the Test page — move to a test-only host that builds one panel in the user simulation with no shell.

**Files:**
- Modify: `src/dstf/authoring/session.py` (`Event`; `Session.__init__`, `open`, `close`; delete `select`; `SessionView`)
- Delete: `src/dstf/authoring/app.py`, `tests/test_shell.py`, `tests/test_author_smoke_playwright.py`, `tests/test_capability_conformance.py` (rewritten in Task 7)
- Modify: `src/dstf/cli/author_cmd.py`
- Create: `tests/capability_host.py`, `tests/test_authoring_imports.py`
- Modify: `tests/test_builtin_capabilities.py`, `tests/test_edit_on_plot.py`, `tests/test_accept_and_rerun.py`

- [ ] **Step 1: Keep the old tests at hand.** `git show 9fd5332:tests/test_shell.py > <scratchpad>/old_test_shell.py` — Task 5 ports the runs-card tests from it. (The sha is the plan's HEAD; the file is also in history.)

- [ ] **Step 2: The host, and the UI-import predicate — both red.** Create `tests/capability_host.py`:

```python
"""A capability's panel alone, in NiceGUI's user simulation with no shell:
the view is built for one `Selection`, the panel's `submit` runs `act` and
hands the outcome to `Session.apply`, and an exception lands in `errors`
under the capability's name. What the old shell added around a panel — the
rebuild on `results`, the leaf's config error above it, the error cleared
in place — returns with the Test page (roadmap §3, §4) and is not emulated
here. Tests import this as `from capability_host import host`."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from nicegui import ui
from nicegui.testing.user_simulation import User, user_simulation

from dstf.authoring.session import Selection, Session, SessionView


class RecordingFigure:
    """`Panel.figure` for the host: records subscriptions so a test can
    dispatch to them by hand; the shell's plot does the real wiring."""

    def __init__(self) -> None:
        self.handlers: list[tuple[str, Callable[[str, dict], None]]] = []

    def on(self, kind: str, handler: Callable[[str, dict], None]) -> None:
        self.handlers.append((kind, handler))


class HostPanel:
    def __init__(self, submit: Callable[..., None], figure: RecordingFigure) -> None:
        self.figure = figure
        self._submit = submit

    def submit(self, **inputs: Any) -> None:
        self._submit(**inputs)


@asynccontextmanager
async def host(
    session: Session, cap: Any, selection: Selection
) -> AsyncIterator[tuple[User, dict[str, str], RecordingFigure]]:
    """Yield ``(user, errors, figure)`` with the panel built at ``/`` — or,
    when ``available`` is not True, a label marked ``reason`` in its place."""
    errors: dict[str, str] = {}
    figure = RecordingFigure()
    view = SessionView(session, selection)

    def submit(**inputs: Any) -> None:
        try:
            session.apply(cap.act(view, **inputs))
            errors.pop(cap.name, None)
        except Exception as e:  # a shell shows this; the host records it
            errors[cap.name] = f"{type(e).__name__}: {e}"

    def root() -> None:
        avail = cap.available(view)
        if avail is not True:
            ui.label(avail if isinstance(avail, str) else "not available").mark("reason")
            return
        cap.panel(view, HostPanel(submit, figure))

    async with user_simulation(root=root) as user:
        await user.open("/")
        yield user, errors, figure
```

Create `tests/test_authoring_imports.py`:

```python
"""Sub-project 1's predicate 5, amended by sub-project 2: every module under
`dstf.authoring` outside `shell/`, `forms.py` and `builtin/` imports no UI
framework. The Session and the contract must import without the `[author]`
extra; the check runs them in a subprocess where `nicegui` cannot import."""

import subprocess
import sys

PROBE = """
import builtins, sys
real = builtins.__import__
def no_ui(name, *a, **k):
    if name == "nicegui" or name.startswith("nicegui."):
        raise ImportError("nicegui is not installed here")
    return real(name, *a, **k)
builtins.__import__ = no_ui
import dstf.authoring  # registers the builtins, imports the Session and the contract
import dstf.authoring.session, dstf.authoring.capabilities, dstf.authoring.spec_document
import dstf.authoring.builtin.accept, dstf.authoring.builtin.rerun
import dstf.authoring.builtin.edit_criteria, dstf.authoring.builtin.edit_tree
import dstf.authoring.builtin.edit_on_plot
assert not any(m == "nicegui" or m.startswith("nicegui.") for m in sys.modules), "nicegui was imported"
print("ok")
"""


def test_the_session_tier_imports_without_a_ui_framework():
    done = subprocess.run(
        [sys.executable, "-c", PROBE], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok"


def test_the_shell_package_is_the_ui_tier():
    """The inverse: the shell package DOES import NiceGUI, and nothing
    else under `authoring/` but `forms.py` and `builtin/` may."""
    import pathlib

    import dstf.authoring

    root = pathlib.Path(dstf.authoring.__file__).parent
    offenders = sorted(
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if "nicegui" in p.read_text(encoding="utf-8")
        and p.parts[len(root.parts)] not in ("shell", "builtin", "forms.py")
    )
    assert offenders == []
```

Run: `uv run --no-sync pytest -q tests/test_authoring_imports.py`. Expected: the first test passes on the unchanged tree; the second FAILS with `offenders == ['app.py']` — the old shell is the file the amended predicate does not admit. Then `uv run --no-sync python -c "import sys; sys.path.insert(0, 'tests'); import capability_host"` succeeds, and `uv run --no-sync python -c "from dstf.authoring.session import Session, SessionView, Selection; SessionView(Session(), Selection())"` FAILS with `TypeError: SessionView.__init__() takes 2 positional arguments but 3 were given` — the red state of the change below.

- [ ] **Step 3: The Session.** In `src/dstf/authoring/session.py`:
  - `Event = Literal["spec", "results", "job", "loading"]`.
  - `Selection`'s docstring: `"""What a page has selected, handed to a capability through its SessionView (D129): the checked tests, the variable and the criterion's tree path. A page builds a new view when it changes; two browser tabs keep two. Not the Session's — that was the old shell's one-tab shape."""`
  - Delete `self.selection = Selection()` in `__init__` (line 173), in `open` (line 254) and in `close` (line 377); delete `select` (lines 387–399) whole.
  - `SessionView`:

```python
class SessionView:
    """What a capability receives: the Session's state and its readers, and
    the selection of the page that built the view — nothing that writes.
    The enforcement is structural — a capability never holds the Session."""

    __slots__ = ("_session", "_selection")

    def __init__(self, session: Session, selection: Selection = Selection()):
        self._session = session
        self._selection = selection

    ...

    @property
    def selection(self) -> Selection:
        return self._selection
```

  (`Selection()` as a default is safe: the dataclass is frozen.) Every other property is unchanged.

- [ ] **Step 4: Delete the old shell and its tests.** `git rm src/dstf/authoring/app.py tests/test_shell.py tests/test_author_smoke_playwright.py tests/test_capability_conformance.py`. In `src/dstf/cli/author_cmd.py`: the `try: from ..authoring.app import run_app` block moves BELOW the `--config` and dropped-flag checks (its docstring already says nothing is resolved before them, and `tests/test_cli.py::test_author_refuses_the_global_flags_it_would_drop` reads those refusals with no shell importable), and imports `from ..authoring.shell import run_app` (the name arrives in Task 5; until then `dstf author` with a valid config raises the extra hint — owner call 3).

- [ ] **Step 5: `tests/test_accept_and_rerun.py`.** Line 49 `view = SessionView(session)` is deleted; line 68 `session.select(tests={CONSTANT_CSV})` becomes `view = SessionView(session, Selection(tests=frozenset({CONSTANT_CSV})))`; import `Selection` beside `Session, SessionView` on line 26. Nothing else in the file selects.

- [ ] **Step 6: `tests/test_edit_on_plot.py`.** The rewrite rule, applied to every test in the file:
  - The `shell` fixture becomes `session`: `cfg, work = _project(tmp_path); s = Session(); s.open(cfg, work_dir=work); return s`; `from dstf.authoring.app import Shell` goes; add `from dstf.authoring.session import Selection, Session, SessionView` and `from capability_host import host`.
  - `_tree_on_d(shell)` / `_root_leaf_on_a(shell, leaf)` take `session` and only stage (their `select` line is deleted). Two module constants replace the selections they set: `ON_D = Selection(tests=frozenset({D}), variable="x")`, `ON_A = Selection(tests=frozenset({A}), variable="x")`.
  - `shell.view` → `SessionView(session, ON_D)` (or `ON_A` where the test staged on A); `shell.session.` → `session.`.
  - `shell.act(cap, **inputs)` followed by `assert shell.errors["edit-on-plot"] == "ValueError: row 0: time is required"` (lines 245–246) becomes `with pytest.raises(ValueError, match="row 0: time is required"): session.apply(EditOnPlot().act(SessionView(session, ON_D), **inputs))`.
  - `test_available_needs_one_test_and_a_handle_bearing_leaf` (line 313): the three `select` + `available` pairs become `cap.available(SessionView(session, Selection(tests=frozenset({A, D}), variable="x"))) == "select one test"` etc., with `Selection(tests=frozenset(), variable="x")` for the empty case.
  - The three panel tests (`test_the_panel_draws_a_table_per_list_field_and_stages_edits`, `test_a_blank_required_cell_is_marked_and_held_until_it_is_filled`, `test_add_row_on_an_empty_points_table`): `async with user_simulation(root=shell.root) as user: await _open_edit_on_plot(shell, user)` becomes `async with host(session, EditOnPlot(), ON_D) as (user, errors, _figure):` — `ON_A` for `test_add_row_on_an_empty_points_table`, which stages on A through `_root_leaf_on_a`; `shell.errors` → `errors`; `_open_edit_on_plot` is deleted.
  - DELETED, named in the roadmap's §3 block by Task 10: `test_a_ring_drag_through_the_hook_stages_and_redraws_in_place`, `test_a_refused_drag_through_the_hook_shows_in_the_slot_and_snaps_back` (the plot, the in-place redraw and the hook's wiring are sub-project 3's), and with them `_shape_index` and `_settled` if nothing else uses them.

- [ ] **Step 7: `tests/test_builtin_capabilities.py`.** The same rule: the `shell` fixture becomes `session`; the imports as in Step 6 plus `from dstf.authoring.builtin.accept import Accept`, `from dstf.authoring.builtin.edit_criteria import EditCriteria`, `from dstf.authoring.builtin.edit_tree import EditTree`, `from dstf.authoring.builtin.rerun import Rerun` at module level (the inline imports go). `LEAF0 = Selection(tests=frozenset({A}), variable="x", leaf_path="/metrics/children/0")`. Per test:
  - `test_edit_criteria_tab_appears_for_a_leaf_and_stages_on_change` → renamed `test_edit_criteria_stages_on_change`; `async with host(session, EditCriteria(), LEAF0) as (user, errors, _):` replaces the simulation and `_select_leaf`; the `should_not_see("Edit criteria")` line goes; the form assertions stay. Every `await user.should_see("2 pending")` in this file (here and in `test_a_mode_switch_starts_from_the_new_modes_defaults`) becomes `assert len(session.spec.pending) == 2` — the host draws no header, and `submit` is synchronous.
  - `test_edit_criteria_switches_mode_and_offers_against_and_window`, `test_a_mode_switch_starts_from_the_new_modes_defaults`, `test_against_offers_*` (three; `_shell_with_soft_check` returns a Session and `_against_select(user)` is unchanged): the host with `LEAF0` (for D-based ones, `Selection(tests=frozenset({D}), variable="x", leaf_path=...)` as the old `select` call named).
  - `test_edit_criteria_is_greyed_while_the_test_is_locked` → keeps its name; asserts `EditCriteria().available(SessionView(session, LEAF0)) == "rerun is running on FakeLib.A"` (the string the old test saw on the tab) with the `_Gate` job, and `is True` after release.
  - `test_a_leaf_config_error_shows_inline_on_the_form` → DELETED (the shell's `_leaf_error`; roadmap §4 by Task 10).
  - `test_edit_tree_needs_exactly_one_test` → `EditTree().available(SessionView(session, Selection(tests=frozenset({A, B})))) == "select exactly one test"` (the string `edit_tree.py` line 73 returns).
  - `test_edit_tree_reaches_nested_nodes`, `test_edit_tree_unwraps_a_single_child_combinator`: `shell.view` → `SessionView(session, <the selection the old select set>)`.
  - `test_edit_tree_wrap_add_remove_and_combinator_edits` → DELETED (roadmap §4 by Task 10): its flow reads the old shell's error label ("validator rejected"), a panel rebuilt after each `results` event (the `remove:/metrics/children/1` button exists only after a rebuild) and the centre's tree rows ("k-of-n k=1 [1]"); the host rebuilds nothing. The tree editor's logic keeps its two pure `act` tests above.
  - `test_accept_is_greyed_without_a_result_and_stores_the_rest` → split: the greyed half is `Accept().available(SessionView(session, Selection(tests=frozenset({C})))) == "re-run before accepting"`; the store half opens `host(session, Accept(), Selection(tests=frozenset({A, B, C})))`, asserts `await user.should_see("re-run before accepting")` (C's row) and clicks the panel's button (`user.find(kind=ui.button, content="Accept").click()` — after Task 4 the button reads the caption `Accept 2 results as baselines`; this task keeps the old `Accept 2` text, and Task 4 updates the `content=`), then the same assertions on notices, verdicts, `ref_0004.json`. The old `await user.should_see(f"{B} · {PASS}", retries=20)` is DROPPED — it read a panel the old shell rebuilt on the write; the `classify(...) == PASS` assertions say the same thing.
  - `test_accept_act_returns_a_store_write_over_the_selection`, `test_accept_is_greyed_while_a_selected_test_is_locked`, `test_accept_is_greyed_when_the_selection_is_a_pending_test`, `test_accept_reports_the_row_reason_and_the_empty_selection`: `shell.view` after a `select` → `SessionView(session, Selection(tests=frozenset({...})))`.
  - `test_accept_excludes_a_simulate_only_test_from_the_count`, `test_accept_excludes_a_result_with_no_tracked_variables`: each selects `{A, B}` and then `{B}` inside one simulation, and one `host` is one selection — so the `{A, B}` half runs under `host(session, Accept(), Selection(tests=frozenset({A, B})))` with its panel assertions, and the `{B}` half becomes `Accept().available(SessionView(session, Selection(tests=frozenset({B}))))` against the string the old test saw on the tab.
  - `_inside` (line 145) is deleted with the leaf-error test, its only caller.
  - `test_rerun_act_returns_a_job_over_the_selection`, `test_rerun_job_run_propagates_an_unbuildable_backend`: the same.
  - `test_rerun_names_the_backend_and_is_greyed_while_a_job_runs` → the host with `Selection(tests=frozenset({A}))`: `(label,) = user.find(marker="rerun-backend").elements; assert label.text == "runs through Python"`; then `Rerun().available(view) == "rerun is running"` under the `_Gate` job and `is True` after.
  - `test_edit_criteria_follows_a_wrap_of_its_leaf_through_the_shell` → DELETED (panel follow; roadmap §4).
  - `test_the_builtins_register_in_order` unchanged.

- [ ] **Step 8: Run green.** `uv run --no-sync pytest -q tests/test_session.py tests/test_builtin_capabilities.py tests/test_edit_on_plot.py tests/test_accept_and_rerun.py tests/test_authoring_imports.py tests/test_figure_hook.py tests/test_cli.py`. Expected: all pass; `tests/test_authoring_imports.py::test_the_shell_package_is_the_ui_tier` passes now that `app.py` is gone.

- [ ] **Step 9: Gates, then commit** — `git add tests/capability_host.py tests/test_authoring_imports.py` first — `src/dstf/authoring/session.py src/dstf/authoring/app.py src/dstf/cli/author_cmd.py tests/test_shell.py tests/test_author_smoke_playwright.py tests/test_capability_conformance.py tests/capability_host.py tests/test_authoring_imports.py tests/test_builtin_capabilities.py tests/test_edit_on_plot.py tests/test_accept_and_rerun.py` with message `session: selection leaves the Session — SessionView(session, selection) (D129); the old shell and its tests go; the builtins' panels are tested through a shell-less host`.

**Named mutations:**
- M3.1: `SessionView.selection` returns `Selection()` regardless of what was passed → `test_accept_is_greyed_without_a_result_and_stores_the_rest` (the caption/count and the store) and `test_rerun_act_returns_a_job_over_the_selection` fail.
- M3.2: `capability_host.host`'s `submit` calls `cap.act` and drops the outcome (no `session.apply`) → the accept store half fails (no baseline for B).
- M3.3 (must survive — a claim about the predicate, verified by applying it): add `import nicegui` at the top of `src/dstf/authoring/session.py` → `test_the_session_tier_imports_without_a_ui_framework` and `test_the_shell_package_is_the_ui_tier` BOTH fail. (Listed under "must catch" in effect: the reviewer applies it and reports both red.)

**Done when:** `SessionView(Session(), Selection())` constructs; `Session` has no attribute `select` or `selection`; `tests/test_authoring_imports.py` passes; every test name listed in Steps 5–7 as kept or renamed exists in its file and passes; `src/dstf/authoring/app.py` is not in the tree; the not-playwright suite is green.

---

### Task 4: The contract: `Placement`, `placements`, `caption`

Decision 2. `Scope(PROJECT, TESTS, VARIABLE, LEAF)` — where a capability's TAB appeared — becomes `Placement(SUITE, TEST, VARIABLE, CRITERION)` — where its BUTTON attaches (brief §3.2). A capability may attach at several (Accept and Rerun at suite and test), so it declares a frozenset. `caption(view)` is the sentence its button carries. `PROJECT` has no successor: the project-less state is unreachable once Close project is gone (brief §3.3), and a recent-projects capability attaches at SUITE later.

**Files:**
- Modify: `src/dstf/authoring/capabilities.py`
- Modify: `src/dstf/authoring/builtin/accept.py`, `rerun.py`, `edit_criteria.py`, `edit_tree.py`, `edit_on_plot.py`
- Modify: `tests/test_capabilities.py`, `tests/test_builtin_capabilities.py`
- Modify: `docs/superpowers/specs/2026-09-08-two-tier-architecture-design.md` (§4.6), `docs/extensibility.md` (the contract paragraph)

- [ ] **Step 1: The contract tests, red.** In `tests/test_capabilities.py` replace the import of `Scope` with `Placement`, give `_Dummy` the new shape, and add three tests:

```python
class _Dummy:
    def __init__(self, name="dummy", placements=frozenset({Placement.SUITE})):
        self.name, self.label, self.placements = name, name.title(), placements

    def available(self, s):
        return True

    def caption(self, s):
        return f"{self.label} {len(s.selection.tests)}"

    def panel(self, s, ui):
        pass

    def act(self, s, **inputs):
        return None
```

(`Scope.TESTS` → `frozenset({Placement.SUITE})` and `Scope.LEAF` → `frozenset({Placement.CRITERION})` in the two existing call sites; `_Half` gains `placements = frozenset({Placement.SUITE})` and its `match="missing act"` becomes `match="missing caption, act"`.)

```python
def test_a_capability_declares_a_non_empty_frozenset_of_placements():
    """The shell draws a button per placement named; a capability naming
    none would register and never appear, and a plain set would let a
    caller mutate a registered capability's placements."""

    class _Nowhere(_Dummy):
        def __init__(self):
            super().__init__("nowhere", frozenset())

    class _Loose(_Dummy):
        def __init__(self):
            super().__init__("loose", {Placement.SUITE})  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="non-empty frozenset of Placement"):
        register_capability(_Nowhere())
    with pytest.raises(TypeError, match="non-empty frozenset of Placement"):
        register_capability(_Loose())
    assert registered_capabilities() == ()


def test_a_capability_may_attach_at_several_placements():
    cap = _Dummy("both", frozenset({Placement.SUITE, Placement.TEST}))
    register_capability(cap)
    assert cap.placements == {Placement.SUITE, Placement.TEST}


def test_plural_counts_its_noun():
    assert plural(0, "test") == "0 tests"
    assert plural(1, "test") == "1 test"
    assert plural(2, "spec edit") == "2 spec edits"
```

(`plural` joins the import list.) Run `uv run --no-sync pytest -q tests/test_capabilities.py`. Expected: `ImportError: cannot import name 'Placement'` — the whole module red.

- [ ] **Step 2: The builtin tests, red.** In `tests/test_builtin_capabilities.py` add, at the end:

```python
def test_the_builtins_declare_their_placements():
    """Brief §3.2: Accept and Rerun at suite and test; Edit tree on the Test
    page's tree; Edit on plot at a variable; Edit criteria at a criterion.
    The placements the Test page (sub-project 3) and the editing merge
    (sub-project 4) build on."""
    from dstf.authoring.builtin.edit_on_plot import EditOnPlot
    from dstf.authoring.capabilities import Placement

    S, T, V, C_ = Placement.SUITE, Placement.TEST, Placement.VARIABLE, Placement.CRITERION
    assert Accept.placements == {S, T} and Rerun.placements == {S, T}
    assert EditTree.placements == {T}
    assert EditOnPlot.placements == {V}
    assert EditCriteria.placements == {C_}


def test_accept_caption_counts_what_it_will_store_and_needs_a_selection(session):
    """DESIGN.md "Buttons": object, effect, count — and at zero the label
    still says what it would do while `available` carries the sentence."""
    three = SessionView(session, Selection(tests=frozenset({A, B, C})))
    assert Accept().caption(three) == "Accept 2 results as baselines"  # C is refused
    one = SessionView(session, Selection(tests=frozenset({D})))
    assert Accept().caption(one) == "Accept 1 result as baselines"
    none = SessionView(session, Selection())
    assert Accept().caption(none) == "Accept 0 results as baselines"
    assert Accept().available(none) == "Accept needs a selection"


def test_rerun_caption_names_count_backend_and_the_selections_unsaved_edits(session):
    one = SessionView(session, Selection(tests=frozenset({A})))
    assert Rerun().caption(one) == "Rerun 1 selected test (Python)"
    assert Rerun().available(SessionView(session, Selection())) == "Rerun needs a selection"
    session.stage([LOOSEN])  # two ops on A: the materialization and the edit
    assert Rerun().caption(one) == "Rerun 1 selected test (Python) (with 2 unsaved edits)"
    # Another model's edits are not this selection's.
    other = SessionView(session, Selection(tests=frozenset({D})))
    assert Rerun().caption(other) == "Rerun 1 selected test (Python)"
```

(`LOOSEN` joins the `test_session` import.) Also update `test_accept_is_greyed_without_a_result_and_stores_the_rest`'s click to `user.find(kind=ui.button, content="Accept 2 results as baselines").click()`, and `test_accept_reports_the_row_reason_and_the_empty_selection` is renamed `test_accept_reports_the_row_reason_the_empty_selection_and_a_selection_naming_no_test`: its empty-selection assertion becomes `== "Accept needs a selection"` (the sentence now answers before the rows are read), and it gains `assert Accept().available(SessionView(session, Selection(tests=frozenset({"Nope.X"})))) == "no successful result in the selection"` — the string that line now reaches only for a selection naming no test of the project. Run `uv run --no-sync pytest -q tests/test_builtin_capabilities.py -k "placements or caption or stores_the_rest"`. Expected: 4 failed — `AttributeError: type object 'Accept' has no attribute 'placements'`, `AttributeError: ... 'caption'` twice, and the store test failing to find the button.

- [ ] **Step 3: The contract.** In `src/dstf/authoring/capabilities.py` replace `Scope` with:

```python
class Placement(Enum):
    """Where a capability attaches (brief §3.2; D129): the Suite page's
    batch bar, the Test page's header, a variable section's toolbar, the
    inline criterion editor. A capability names the frozenset it attaches
    at; the shell draws a button carrying its ``caption`` at each and knows
    no capability by name. The tab strip this replaces listed one tab per
    ``Scope``; the scopes' selection rules (TESTS needs a selection, LEAF a
    leaf) are now what ``available`` answers for the page's selection."""

    SUITE = "suite"
    TEST = "test"
    VARIABLE = "variable"
    CRITERION = "criterion"


def plural(count: int, noun: str) -> str:
    """``"2 tests"``, ``"1 test"``: the count and its noun for a caption.
    Regular plurals only — every noun a caption uses here is one."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"
```

The protocol:

```python
@runtime_checkable
class Capability(Protocol):
    name: str
    label: str
    placements: frozenset[Placement]

    def available(self, s: SessionView) -> bool | str:
        """``True``, or ``False`` / the sentence the greyed button carries —
        why it is unavailable and what would enable it ("Accept needs a
        selection", "rerun is running on FakeLib.A")."""
        ...

    def caption(self, s: SessionView) -> str:
        """The button's label: its object, its effect and a count
        ("Rerun 2 selected tests (Dymola)"). At zero it still says what it
        would do; ``available`` then carries the sentence."""
        ...

    def panel(self, s: SessionView, ui: Panel) -> None: ...

    def act(self, s: SessionView, **inputs: Any) -> Outcome | Sequence[Outcome]: ...
```

`register_capability`:

```python
def register_capability(cap: Capability) -> Capability:
    """Register ``cap`` under its name; returns it. Loud on a duplicate —
    two capabilities sharing a name would make the shell's button order
    depend on import order — and on a shape the shell cannot draw."""
    if not isinstance(cap, Capability):
        missing = [
            a
            for a in ("name", "label", "placements", "available", "caption", "panel", "act")
            if not hasattr(cap, a)
        ]
        raise TypeError(
            f"{cap!r} is not a Capability: missing {', '.join(missing) or 'a method'}"
        )
    placements = cap.placements
    if (
        not isinstance(placements, frozenset)
        or not placements
        or not all(isinstance(p, Placement) for p in placements)
    ):
        raise TypeError(
            f"capability {cap.name!r}: placements must be a non-empty frozenset of "
            f"Placement, got {placements!r}"
        )
    if cap.name in _CAPABILITIES:
        raise ValueError(f"capability {cap.name!r} is already registered")
    _CAPABILITIES[cap.name] = cap
    return cap
```

The module docstring's "lists by scope" becomes "draws at the placements it names"; `Panel`'s docstring loses "(sub-project 4 provides the NiceGUI one)" and says "the shell's `ShellPanel`". Everything else in the file is unchanged.

- [ ] **Step 4: The builtins.**
  - `accept.py`: `from ..capabilities import Panel, Placement, StoreWrite, plural`; `placements = frozenset({Placement.SUITE, Placement.TEST})` replaces `scope`; in `available`, after the project check: `if not s.selection.tests: return "Accept needs a selection"`; add

```python
    def caption(self, s: SessionView) -> str:
        count = sum(1 for _, _, action in _rows(s) if action == WILL_STORE)
        return f"Accept {plural(count, 'result')} as baselines"
```

  and the panel's button becomes `ui.button(self.caption(s), on_click=lambda: ui_panel.submit()).props("dense no-caps")` (the local `count` line goes). The module docstring's "TESTS scope" → "SUITE and TEST placements"; "The tab's own availability" → "The button's own availability".
  - `rerun.py`: `from ..capabilities import Job, JobContext, Panel, Placement, Scope→(gone), plural`; `placements = frozenset({Placement.SUITE, Placement.TEST})`; in `available`, after the project check: `if not s.selection.tests: return "Rerun needs a selection"`; add

```python
    def caption(self, s: SessionView) -> str:
        """Count, backend, and the selection's unsaved edits (brief §3.6: a
        run uses the spec with pending edits applied — `stage` re-merges
        `project.tests` — and the button says so)."""
        project = s.project
        backend = project.config.backend_name if project is not None else "?"
        models = set(_models(s))
        text = f"Rerun {plural(len(models), 'selected test')} ({backend})"
        edits = sum(1 for op in s.pending if op.model_id in models)
        if edits:
            text += f" (with {plural(edits, 'unsaved edit')})"
        return text
```

  and the panel's button `ui.button(self.caption(s), ...)`. Docstring: "TESTS scope" → "SUITE and TEST placements"; "the tab says so" → "the button says so".
  - `edit_criteria.py`: `placements = frozenset({Placement.CRITERION})`; `def caption(self, s): return self.label`. `edit_tree.py`: `frozenset({Placement.TEST})`, the same `caption`. `edit_on_plot.py`: `frozenset({Placement.VARIABLE})`, the same `caption`. Each imports `Placement` where it imported `Scope`; each docstring's "LEAF scope" / "TESTS scope" / "VARIABLE scope" names the placement instead. Their `available` texts ("select one test", "no criterion on x has on-plot handles") are unchanged — sub-project 4 rewrites these three.

- [ ] **Step 5: Run green.** `uv run --no-sync pytest -q tests/test_capabilities.py tests/test_builtin_capabilities.py tests/test_edit_on_plot.py tests/test_accept_and_rerun.py tests/test_authoring_imports.py`. Expected: all pass. Then `uv run --no-sync python -c "import dstf.authoring.capabilities as c; assert not hasattr(c, 'Scope'); from dstf.authoring.capabilities import Placement; assert [p.value for p in Placement] == ['suite', 'test', 'variable', 'criterion']"`.

- [ ] **Step 6: The spec and the contract doc.** Append to §4.6 of `docs/superpowers/specs/2026-09-08-two-tier-architecture-design.md`, after the "Amended in sub-project 6" text and before "### 4.7":

```markdown
**Amended in the redesign's sub-project 2 (2026-09-16; D129).** The tab strip
is gone (brief §3.2, DESIGN.md "Tabs: none"), and with it `Scope`. A
capability declares `placements: frozenset[Placement]` — `SUITE` (the Suite
page's batch bar), `TEST` (the Test page's header), `VARIABLE` (a variable
section's toolbar), `CRITERION` (the inline criterion editor) — and
`caption(view) -> str`, the sentence its button carries: object, effect and
count ("Rerun 2 selected tests (Dymola)"), still saying what it would do at
zero while `available` carries the reason the button is greyed ("Rerun needs a
selection"). The shell draws one outlined button per registered capability at
each placement it names and hosts its `panel` in a dialog the button opens;
`submit` there runs `act` and hands the outcome to the Session as before. It
still knows no capability by name. `PROJECT` has no successor: Close project is
gone, so the project-less state is unreachable; a recent-projects capability
attaches at `SUITE` when it comes. `Panel.figure` at a placement without a
plot raises on `on` — a subscription there is a contract misuse.

Selection is the page's, not the Session's: `SessionView(session, selection)`
carries the `Selection` (tests, variable, criterion path) of the page that
built the view, one per browser tab. `Session.select`, `Session.selection` and
the `selection` event are deleted; a capability's `available`, `caption`,
`panel` and `act` read `s.selection` exactly as before. Accept and Rerun attach
at `SUITE` and `TEST`; Edit tree at `TEST`; Edit on plot at `VARIABLE`; Edit
criteria at `CRITERION` — the last three re-hosted by sub-projects 3 and 4.
```

In `docs/extensibility.md`, the sentence "the `Capability` protocol (`name`, `label`, `scope`, and `available` / `panel` / `act`), the `Scope` enum (`PROJECT`, `TESTS`, `VARIABLE`, `LEAF`)" becomes "the `Capability` protocol (`name`, `label`, `placements`, and `available` / `caption` / `panel` / `act`), the `Placement` enum (`SUITE`, `TEST`, `VARIABLE`, `CRITERION` — where the capability's button attaches; a capability names a frozenset of them)", and a sentence follows the outcomes paragraph: "`caption(view)` is the button's label — object, effect and count, `plural()` for the noun — and `available` the sentence beside it when it is greyed. The view's `selection` is the page's that built it (D129): `SessionView(session, selection)`."

- [ ] **Step 7: Gates, then commit** `src/dstf/authoring/capabilities.py src/dstf/authoring/builtin/accept.py src/dstf/authoring/builtin/rerun.py src/dstf/authoring/builtin/edit_criteria.py src/dstf/authoring/builtin/edit_tree.py src/dstf/authoring/builtin/edit_on_plot.py tests/test_capabilities.py tests/test_builtin_capabilities.py docs/superpowers/specs/2026-09-08-two-tier-architecture-design.md docs/extensibility.md` with message `capabilities: Scope becomes Placement (suite/test/variable/criterion); a capability declares its placements and its caption`.

**Named mutations:**
- M4.1: `register_capability` skips the `caption` attribute check → `test_registering_a_non_capability_is_loud_at_registration` (`missing caption, act`) fails.
- M4.2: the placements check accepts an empty frozenset → `test_a_capability_declares_a_non_empty_frozenset_of_placements` fails.
- M4.3: `Accept.caption` counts every row (`len(_rows(s))`) → "Accept 3 results" ≠ 2.
- M4.4: `Rerun.caption` counts every pending op (`len(s.pending)`) → the D selection reads "(with 2 unsaved edits)".
- M4.5: `Rerun.available` returns True for an empty selection → `"Rerun needs a selection"` assertion fails.

**Done when:** the interpreter check of Step 5 passes; the five builtins report the placements the test names; the spec and extensibility.md carry the amendment text; the suite is green.

---

### Task 5: The shell as a process, the header, the runs card, `run_app`

Decisions 8 and 9. `Shell` owns the Session, the thread that opens the project, the queue every Session event crosses to reach the pages, the app-level timer that drains it, and the live pages. The header both pages share; the pending drawer; the runs card (the old footer's content, as a Suite card until sub-project 5); `run_app`. The Suite page exists after this task with its header, progress line, CLI hint and runs card — the list is Task 6, the batch buttons Task 7.

**Files:**
- Create: `src/dstf/authoring/shell/shell.py`, `style.py`, `widgets.py`, `header.py`, `runs.py`, `suite.py`, `app.py`
- Modify: `src/dstf/authoring/shell/__init__.py` (exports `run_app`), `tests/test_cli.py` (module names), `docs/architecture.md`, `CLAUDE.md`
- Create: `tests/test_shell.py`

- [ ] **Step 1: The tests, red.** Create `tests/test_shell.py`:

```python
"""The new shell (roadmap §2; D129) in NiceGUI's in-process user simulation,
no browser: the process (`Shell`), the header, Save / Discard / Export, Stop
server, the pending drawer, the dashboard route, the progressive open and
the runs card. The Suite list is tests/test_suite_page.py; the buttons at a
placement tests/test_capability_conformance.py."""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

pytest.importorskip("nicegui")

from nicegui import app  # noqa: E402
from nicegui.testing.user_simulation import user_simulation  # noqa: E402

from dstf.authoring import capabilities as caps  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.capabilities import Job  # noqa: E402
from dstf.authoring.shell.shell import Shell  # noqa: E402
from test_session import LOOSEN, A, B, C, D, G, _Gate, _project  # noqa: E402


@pytest.fixture(autouse=True)
def _registry():
    """The builtins register on `import dstf.authoring`; each test starts
    from that state and leaves it."""
    caps._reset_for_tests()
    register_builtins()
    yield
    caps._reset_for_tests()
    register_builtins()


@pytest.fixture
def shell(tmp_path):
    """A shell whose project is open synchronously — what a page sees once
    loading is over. The open's events sit in the shell's queue until the
    first tick replays them (scan → spec → results → done) inside that tick
    — and the first tick runs while the page waits for its handshake, so a
    page built here sees the end state. The progressive open has its own
    tests below."""
    cfg, work = _project(tmp_path)
    s = Shell(cfg, work_dir=work)
    s.session.open(cfg, work_dir=work)
    s.load.phase = "done"
    return s


async def _settled(predicate, tries: int = 60) -> None:
    """Events reach a page on the shell's tick (0.1 s); wait for one to land."""
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0.05)
    assert predicate()


def _one(user, marker: str):
    (element,) = user.find(marker=marker).elements
    return element


async def _open(user, path: str = "/") -> None:
    """Open the page AND wait for it to be built: every page awaits its
    client's handshake before drawing, and `user.open` returns as soon as
    the handshake is sent — the page resumes on the next loop iteration.
    The header's wordmark is the first element every page draws."""
    await user.open(path)
    await user.should_see(marker="wordmark", retries=20)


def _suite(shell, user):
    """The Suite page object built for the user's CURRENT client. A page the
    user navigated away from is still registered — the simulation never
    deletes its client, only a browser disconnect does — so the shell may
    hold several."""
    from dstf.authoring.shell.suite import SuitePage

    (page,) = [
        p for p in shell.pages if isinstance(p, SuitePage) and p.client is user.client
    ]
    return page


# -- the header ------------------------------------------------------------


async def test_the_header_names_the_product_the_library_and_the_backend(shell):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        assert _one(user, "wordmark").text == "DSTF"
        assert _one(user, "project").text == "FakeLib"  # config.library_name
        backend = _one(user, "backend")
        assert backend.value == "Python" and backend.options == ["Python"]
        assert _one(user, "runs").text == "idle"


async def test_save_discard_and_export_say_their_object_effect_and_count(shell):
    """DESIGN.md "Buttons": the label names object, effect and count; at
    zero it still says what it would do and the sentence beside it says
    why it is disabled."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        save, discard, export = (_one(user, m) for m in ("save", "discard", "export"))
        assert save.text == "Save 0 spec edits (0 tests) to test_spec.json"
        assert not save.enabled and not discard.enabled and not export.enabled
        assert _one(user, "save:why").text == "no pending edits"
        assert _one(user, "pending").text == "0 pending edits"
        shell.session.stage([LOOSEN])
        await _settled(lambda: save.enabled)
        assert save.text == "Save 2 spec edits (1 test) to test_spec.json"
        assert discard.text == "Discard 2 edits" and discard.enabled
        assert export.text == "Export 2 edits to spec_patch.json" and export.enabled
        assert _one(user, "pending").text == "2 pending edits"
        # Hidden elements are not found by `find`; the why-line is read
        # through the page object.
        assert not _suite(shell, user).header.save.why.visible


async def test_export_discard_and_save_act_on_the_session_and_the_disk(shell):
    spec_path = shell.session.spec.path
    work_dir = shell.session.project.config.work_dir

    def entry_a() -> dict:
        tests = json.loads(spec_path.read_text(encoding="utf-8"))["tests"]
        return next(e for e in tests if e["model"] == A)

    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.session.stage([LOOSEN])
        await _settled(lambda: _one(user, "export").enabled)
        user.find(marker="export").click()
        envelope = json.loads((work_dir / "spec_patch.json").read_text(encoding="utf-8"))
        assert [p["model"] for p in envelope["patches"]] == [A]
        user.find(marker="discard").click()
        await _settled(lambda: not shell.session.spec.pending)
        assert "metrics" not in entry_a()
        shell.session.stage([LOOSEN])
        await _settled(lambda: _one(user, "save").enabled)
        user.find(marker="save").click()
        await _settled(lambda: not shell.session.spec.pending)
        assert entry_a()["metrics"]["children"][0]["tolerance"] == 0.5


async def test_save_refused_on_a_changed_file_offers_reload(shell):
    spec_path = shell.session.spec.path
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.session.stage([LOOSEN])
        await _settled(lambda: _one(user, "save").enabled)
        spec_path.write_text(
            spec_path.read_text(encoding="utf-8") + "\n", encoding="utf-8"
        )
        user.find(marker="save").click()
        await user.should_see("changed on disk")
        assert shell.session.spec.pending  # kept
        user.find("Reload").click()
        await _settled(lambda: not shell.session.spec.pending)


async def test_stop_server_confirms_with_its_reasons_and_stops_on_the_second_word(
    shell, monkeypatch
):
    """Brief §3.3: Stop server confirms when edits are pending or a run is in
    flight, naming both."""
    stopped: list[bool] = []
    monkeypatch.setattr(app, "shutdown", lambda: stopped.append(True))
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.session.stage([LOOSEN])
        gate = _Gate()
        shell.session.apply(Job(run=gate, label="rerun", models=frozenset({D})))
        await _settled(lambda: _one(user, "pending").text == "2 pending edits")
        user.find(marker="stop").click()
        await user.should_see("2 unsaved edits will be lost")
        await user.should_see("rerun is still running")
        assert stopped == []
        user.find("Keep running").click()
        user.find(marker="stop").click()
        await user.should_see("Stop anyway")
        user.find(marker="stop-anyway").click()
        assert stopped == [True]
        gate.release.set()
        await _settled(lambda: shell.session.running is None)


async def test_stop_server_stops_at_once_with_nothing_to_lose(shell, monkeypatch):
    stopped: list[bool] = []
    monkeypatch.setattr(app, "shutdown", lambda: stopped.append(True))
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        user.find(marker="stop").click()
        assert stopped == [True]
        await user.should_not_see("Stop the server?")


async def test_the_pending_drawer_lists_staged_ops_by_model(shell):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        await user.should_see("nothing staged")
        shell.session.stage([LOOSEN])
        await user.should_see(marker="drawer-op", retries=20)  # `find` raises on absence
        await _settled(lambda: len(user.find(marker="drawer-op").elements) == 2)
        texts = [e.text for e in user.find(marker="drawer-op").elements]
        assert texts[0].startswith("add /metrics ")
        assert texts[1].startswith("add /metrics/children/0/tolerance 0.5")
        assert _one(user, "drawer-model").text == A


async def test_the_dashboard_route_serves_the_open_projects_work_dir(shell):
    work_dir = shell.session.project.config.work_dir
    (work_dir / "dashboard.html").write_text("<h1>dash</h1>", encoding="utf-8")
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        ok = await user.http_client.get("/dashboard/dashboard.html")
        assert ok.status_code == 200 and "dash" in ok.text
        missing = await user.http_client.get("/dashboard/nothing.html")
        assert missing.status_code == 404


# -- the progressive open --------------------------------------------------


def _gate_the_read(monkeypatch) -> threading.Event:
    """`Backend.read_last_results` hands each result on only once the gate is
    set, so the open's in-between state — project set, nothing scored — is
    observable."""
    from dstf.backends.base import Backend

    real = Backend.read_last_results
    gate = threading.Event()

    def gated(self, tests, on_result=None):
        def slow(model_id, result):
            assert gate.wait(10)
            if on_result is not None:
                on_result(model_id, result)

        return real(self, tests, slow)

    monkeypatch.setattr(Backend, "read_last_results", gated)
    return gate


async def test_the_page_appears_while_the_project_opens_and_says_where_it_stands(
    tmp_path, monkeypatch
):
    """Brief §3.8: `open_async` runs the Session's open on a thread; the page
    is built before a result is read, and its progress line follows the
    loading events."""
    gate = _gate_the_read(monkeypatch)
    cfg, work = _project(tmp_path)
    shell = Shell(cfg, work_dir=work)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        assert _one(user, "project").text == "no project"
        await user.should_see("dstf author --config")  # the hint, until something opens
        shell.open_async()
        await _settled(lambda: shell.session.project is not None)
        progress = _suite(shell, user).progress  # hidden while empty: not `find`able
        await _settled(lambda: progress.text == "scoring results… 0 of 5")
        assert _one(user, "project").text == "FakeLib"  # the header follows the spec event
        assert shell.load.in_progress
        gate.set()
        await _settled(lambda: shell.load.phase == "done")
        await _settled(lambda: progress.text == "")
        assert {A, B, C, D, G} <= set(shell.session.comparisons)
        assert shell.load.error is None


async def test_an_open_that_fails_says_so_on_the_page(tmp_path):
    """Decision 8: a backend this machine cannot build (here a name nobody
    registered) leaves no project; the page says why and keeps the hint."""
    cfg, work = _project(tmp_path)
    shell = Shell(cfg, work_dir=work)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.open_async("NoSuchBackend")
        await _settled(lambda: shell.load.phase == "failed")
        await user.should_see("could not open the project", retries=20)
        await user.should_see("NoSuchBackend")
        await user.should_see("dstf author --config")
        assert shell.session.project is None


async def test_a_partial_scoring_failure_opens_the_project_with_a_warning(
    tmp_path, monkeypatch
):
    """Task 1's `done` in a `finally`, seen from the shell: the project is
    open, the failed model is on the shell's list of unscored models, and
    the failure is a warning line — not a failed open."""
    import dstf.authoring.session as session_mod

    real = session_mod.compare_one

    def failing(test, *args, **kwargs):
        if test.model_id == D:
            raise ValueError("boom")
        return real(test, *args, **kwargs)

    monkeypatch.setattr(session_mod, "compare_one", failing)
    cfg, work = _project(tmp_path)
    shell = Shell(cfg, work_dir=work)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.open_async()
        await _settled(
            lambda: shell.load.phase == "done" and shell.load.error is not None
        )
        assert shell.session.project is not None
        assert D not in shell.session.comparisons and shell.load.failed_models == [D]
        await user.should_see("warning: RuntimeError: scoring FakeLib.D failed", retries=20)
```

Then port the runs-card tests from `<scratchpad>/old_test_shell.py` (Task 3 Step 1; else `git show 9fd5332:tests/test_shell.py`), appended under a `# -- the runs card --` divider, with the helpers they call — `_write_status` (old line 162) verbatim and `import time` — and these two substitutions beyond the rule below: `shell._refresh("jobs")` (twice in `test_a_malformed_status_snapshot_renders_what_it_has`) → `_suite(shell, user).runs.refresh()`; the `Close project` click in `test_the_last_jobs_outcome_is_the_projects_and_goes_with_it` → `shell.session.close()` (the header has no such button; the Session keeps the method), after which the port keeps the old `should_not_see` lines, which do the waiting (`no job running` is drawn whenever no job runs, so a `should_see` of it proves nothing about the close), and the old `should_see("no project open", retries=20)` now passes through the header's `backend:why` label — the card must show no `finished:` line, notices or artifacts; the runs card follows `spec` for exactly this: `test_a_thread_posted_completion_reaches_the_page`, `test_the_job_panel_shows_per_test_rows_from_the_running_status`, `test_a_status_from_an_older_run_is_not_this_jobs_progress`, `test_a_job_that_raises_shows_its_failure_and_the_message`, `test_a_store_writes_line_is_shown_in_the_footer` (renamed `…_in_the_runs_card`), `test_a_finished_jobs_failed_result_shows_its_error_message`, `test_a_failed_job_shows_no_result_rows_from_before_it`, `test_a_job_that_reloads_nothing_shows_no_result_rows`, `test_a_malformed_status_snapshot_renders_what_it_has`, `test_the_last_jobs_outcome_is_the_projects_and_goes_with_it`. The port rule: `root=shell.root` → `root=shell.index`; a `Shell()` + `s.open(cfg, work_dir=work)` in a test body → `Shell(cfg, work_dir=work)` + `s.session.open(cfg, work_dir=work)` + `s.load.phase = "done"`; any `shell.session.select(...)` line deleted (no runs-card test reads a selection); EVERY `should_see` / `should_not_see` whose text is tick-delivered (anything after an `apply`, a `stage`, a `close` or a gate release: `stored 1`, `running: rerun`, `finished: `, `failed: `, `no job running`, `no project open`, the `job-row` texts) gets `retries=20` — the bare default is three checks, 0.3 s, and M5.6's `TICK = 0.5` must survive; the strings `no job running`, `running: `, `finished: `, `failed: `, the `job-row` marker and the notices/artifacts are unchanged by this task. Their docstrings' "footer" → "runs card".

Also append to `tests/test_config.py` — the header's backend select reads `Config.backends`, which Step 2 adds:

```python
def test_config_keeps_the_backends_map_for_the_tools_backend_select(tmp_path):
    """The authoring shell offers testing.json's `backends` names as the
    backends to switch to (brief §3.6); until now the map was read for the
    executable path and dropped."""
    (tmp_path / "testing.json").write_text(
        json.dumps(
            {
                "source_type": "python",
                "source_path": ".",
                "library_name": "L",
                "backend": "Python",
                "backends": {
                    "Python": ["python3"],
                    "Dymola 2026x": ["C:/Program Files/Dymola 2026x/bin64/Dymola.exe"],
                },
            }
        ),
        encoding="utf-8",
    )
    config = Config(config_file=tmp_path / "testing.json")
    assert config.backends == {
        "Python": ["python3"],
        "Dymola 2026x": ["C:/Program Files/Dymola 2026x/bin64/Dymola.exe"],
    }
```

(`json` and `Config` are already imported there.)

Run, as TWO commands (a collection error interrupts the whole run, so a combined command would show only the first): `uv run --no-sync pytest -q tests/test_shell.py`. Expected: collection error — `ModuleNotFoundError: No module named 'dstf.authoring.shell.shell'`. Then `uv run --no-sync pytest -q tests/test_config.py -k backends_map`. Expected: `AttributeError: 'Config' object has no attribute 'backends'`. (The 11 shell tests above are the reproducers; each fails on that import until the module exists, and the reviewer applies the mutations for the per-test red states.)

Also in `tests/conftest.py`: `_SESSION_THREADS = ("dstf-overlay-index", "dstf-rescan", "dstf-open")` — the shell's open thread outlives a test that fails mid-open (the gated read waits 10 s), and `_scan_cache_home`'s redirect must not be undone while `store_scan` still runs on it; the comment above the tuple gains "and the shell's open thread".

- [ ] **Step 2: `Config.backends`.** In `src/dstf/config.py`, after the `disabled_bundled` field: 

```python
    # testing.json's `backends` map, kept whole for the authoring shell's
    # backend select (brief §3.6); the executable-path resolution below reads
    # the same map.
    backends: dict[str, list[str]] = field(default_factory=dict)
```

and in `__post_init__`, directly before `# Resolve backend executable path`: `if not self.backends: self.backends = {name: list(paths) for name, paths in (file_config.get("backends") or {}).items()}`.

- [ ] **Step 3: `style.py`.**

```python
"""The shell's stylesheet on top of `reporting.theme.css_variables()`
(DESIGN.md "Typography", "Header", "Spacing, shape, elevation", "Status
badges"): the font token on the body and on Quasar's controls — the
roadmap left the tool on Quasar's stack until this shell applied it — the
off-white header with its hairline, the 32 px rows with their status tint,
the filled badge, the sentence beside a greyed button. Every colour is a
token; nothing here names a hex."""

from __future__ import annotations

from ...reporting.theme import STATUS

_TINTS = "".join(
    f".dstf-tint-{status} {{ background: var(--dstf-{status}-tint); }}\n"
    for status in STATUS
)

STYLE = (
    """
body, .q-header, .q-drawer, .q-btn, .q-field, .q-badge, .q-dialog, .q-tooltip, .q-item {
  font-family: var(--dstf-font);
}
body { background: var(--dstf-page); color: var(--dstf-text); }
.dstf-header.q-header {
  background: var(--dstf-page) !important; color: var(--dstf-text) !important;
  border-bottom: 1px solid var(--dstf-border); box-shadow: none;
  padding: 4px 24px; min-height: 40px;
}
.dstf-wordmark { font-size: 20px; font-weight: 700; }
.dstf-mono { font-family: var(--dstf-mono); }
.dstf-why { color: var(--dstf-text-muted); font-size: 12px; }
.dstf-progress { font-size: 12px; color: var(--dstf-text-secondary); }
.dstf-group {
  font-size: 10px; text-transform: uppercase; letter-spacing: .03em;
  color: var(--dstf-text-muted); padding: 12px 0 2px;
}
.dstf-row {
  min-height: 32px; border-bottom: 1px solid var(--dstf-border-hairline);
  background: var(--dstf-surface); transition: background .1s;
}
.dstf-row:hover { background: var(--dstf-surface-hover); }
.dstf-badge.q-badge {
  color: #ffffff; font-size: 10.5px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .03em; border-radius: 3px;
}
.dstf-chip-active { outline: 2px solid var(--dstf-accent); }
.dstf-card {
  border: 1px solid var(--dstf-border); border-radius: 4px; box-shadow: none;
  background: var(--dstf-surface); padding: 16px;
}
"""
    + _TINTS
)
```

(`#ffffff` on the badge is DESIGN.md's "white text on the status fill" — the one literal, on purpose.)

- [ ] **Step 4: `widgets.py`.**

```python
"""`Action`: a button whose label is its caption and whose disabled state
carries the sentence that says why and what enables it, beside it
(DESIGN.md "Disabled states explain themselves": never a bare grey
button). The header's Save / Discard / Export and every capability's
button at a placement are Actions."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui


class Action:
    def __init__(
        self,
        on_click: Callable[[], None],
        *,
        props: str,
        marker: str,
        why: bool = True,
    ) -> None:
        with ui.row().classes("items-center no-wrap gap-2") as self.container:
            self.button = ui.button(on_click=on_click).props(props).mark(marker)
            self.why: ui.label | None = (
                ui.label().classes("dstf-why").mark(f"{marker}:why") if why else None
            )

    def set(self, caption: str, reason: str | None) -> None:
        """``reason`` None enables the button; a sentence disables it and is
        shown beside it (when this Action carries a why-line)."""
        self.button.set_text(caption)
        if reason is None:
            self.button.enable()
        else:
            self.button.disable()
        if self.why is not None:
            self.why.set_text(reason or "")
            self.why.set_visibility(reason is not None)
```

- [ ] **Step 5: `shell.py`.**

```python
"""The shell as a process (roadmap §2; D129): one Session, one thread that
opens the project, one queue every Session event crosses on its way to the
pages, one app-level timer that drains it, the pages a browser has open,
and the child processes "Open in another backend…" starts. It knows no
capability by name: `placed` lists what is registered at a placement with
each one's answer to `available`. Nothing here is a page — the pages are
`suite.py` and `test_page.py`, one object per browser tab."""

from __future__ import annotations

import logging
import queue
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote

from fastapi.responses import FileResponse, JSONResponse
from nicegui import app, ui

from ...cli.common import _filter_tests
from ...reporting.theme import css_variables, quasar_palette
from ..capabilities import Capability, Placement, plural, registered_capabilities
from ..session import Selection, Session, SessionView
from .style import STYLE

logger = logging.getLogger(__name__)

TICK = 0.1  # seconds; a Session event reaches every page within one


class Page(Protocol):
    """What the shell fans events out to: a live page and its client."""

    client: Any  # nicegui.Client; `is_deleted` prunes the page

    def handle(self, event: dict[str, Any]) -> None: ...


@dataclass
class LoadState:
    """Where the open stands, kept by the shell from the Session's `loading`
    events (decision 9) and read by every page's progress line."""

    phase: str = "idle"  # idle | scan | results | done | failed
    done: int = 0
    total: int = 0
    cached: bool = False
    #: The last open's failure, or a warning from an open that completed —
    #: one model's scoring raised; a reopen that could not build its backend
    #: and left the project as it was.
    error: str | None = None
    #: Models announced with `error=True`: scored by nobody, drawn as such.
    failed_models: list[str] = field(default_factory=list)

    @property
    def in_progress(self) -> bool:
        return self.phase in ("scan", "results")

    def text(self) -> str:
        """The progress line (brief §3.8: "scanning library… N tests found")."""
        if self.phase == "scan":
            if self.cached:
                return f"library scan from cache: {plural(self.total, 'test')}"
            if self.total:
                return f"scanning library… {self.done} of {self.total}"
            return "scanning library…"
        if self.phase == "results":
            return f"scoring results… {self.done} of {self.total}"
        if self.phase == "failed":
            return f"could not open the project: {self.error}"
        if self.error:
            return f"warning: {self.error}"
        return ""


class Shell:
    def __init__(
        self,
        config_path: Path,
        *,
        work_dir: Path | None = None,
        filter: str | None = None,
        package: str | None = None,
        show_browser: bool = True,
    ) -> None:
        self.config_path = Path(config_path)
        self.work_dir = work_dir
        self.filter, self.package = filter, package
        self.show_browser = show_browser
        # What worker threads post — a job's completion, the rescan's merge:
        # Session mutations that must run on the UI loop. `tick` runs them.
        self._posted: queue.Queue[Callable[[], None]] = queue.Queue()
        self.session = Session(post=self._posted.put)
        # Every Session event, from whichever thread emitted it, crosses here
        # and is fanned out to the pages on the UI loop by `tick`.
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        for event in ("spec", "results", "job", "loading"):
            self.session.on(event, self._events.put)
        self.pages: list[Page] = []
        self.load = LoadState()
        self._open_thread: threading.Thread | None = None
        self.children: list[subprocess.Popen[bytes]] = []
        self._mounted = False
        self._timer: Any = None
        self._ticks = 0

    # -- the open ------------------------------------------------------------

    def open_async(self, backend: str | None = None) -> None:
        """Open the project — or reopen it on `backend` (brief §3.6) — on a
        thread; the pages follow the `loading` events and the `opened`
        event this shell adds at the end. Refused while an open runs.
        `Session.open` itself refuses while edits are pending or a job runs,
        and that refusal arrives as the `opened` event's error."""
        if self._open_thread is not None and self._open_thread.is_alive():
            raise RuntimeError("the project is still opening")
        self.load = LoadState(phase="scan")
        thread = threading.Thread(
            target=self._open_worker, args=(backend,), name="dstf-open", daemon=True
        )
        self._open_thread = thread
        thread.start()

    def _open_worker(self, backend: str | None) -> None:
        error: str | None = None
        try:
            self.session.open(self.config_path, backend=backend, work_dir=self.work_dir)
        except Exception as e:  # shown on the page (decision 8), never lost on a thread
            logger.exception("opening %s failed", self.config_path)
            error = f"{type(e).__name__}: {e}"
        self._events.put({"event": "opened", "error": error})

    # -- the loop --------------------------------------------------------------

    def start(self) -> None:
        """The app-level timer: one per process, once the event loop runs. A
        stopped app nulls its timers' callbacks (`Timer._cleanup`), so a
        Shell that outlives one app run gets a new timer with the next."""
        if self._timer is None or self._timer.callback is None:
            self._timer = app.timer(TICK, self.tick)

    def register(self, page: Page) -> None:
        self.pages.append(page)

    def tick(self) -> None:
        """Run what worker threads posted, then hand every queued Session
        event to every live page — a page whose client is gone is dropped.
        Once a second while a job runs, a `poll` event, so the runs card
        re-reads `status.json` (§4.7)."""
        while True:
            try:
                fn = self._posted.get_nowait()
            except queue.Empty:
                break
            fn()
        events: list[dict[str, Any]] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                break
        self._ticks += 1
        every = max(1, round(1.0 / TICK))  # once a second, whatever the tick
        if self.session.running is not None and self._ticks % every == 0:
            events.append({"event": "poll"})
        # `is_deleted` is set by the browser's disconnect (after NiceGUI's
        # reconnect timeout); a page navigated away from in the user
        # simulation is never deleted, so tests pick a page by its client.
        self.pages = [p for p in self.pages if not p.client.is_deleted]
        for event in events:
            self._absorb(event)
            for page in list(self.pages):
                try:
                    page.handle(event)
                except Exception:  # one page's failure is not another's
                    logger.exception("a page failed on %r", event.get("event"))

    def _absorb(self, event: dict[str, Any]) -> None:
        """`LoadState` from the events (decision 9)."""
        kind = event["event"]
        load = self.load
        if kind == "loading":
            phase = event.get("phase")
            if phase == "scan":
                if load.phase != "scan":
                    # An open this shell did not start (an `OpenProject`
                    # outcome runs `Session.open` on the UI loop): a fresh
                    # state, so the previous project's failures do not carry.
                    load = self.load = LoadState(phase="scan")
                load.cached = bool(event.get("cached"))
                load.done, load.total = int(event.get("done", 0)), int(event.get("total", 0))
            elif phase == "results":
                load.phase = "results"
                load.done, load.total = int(event["done"]), int(event["total"])
                if event.get("error"):
                    load.failed_models.append(str(event["model"]))
            elif phase == "done":
                load.phase = "done"
        elif kind == "spec" and load.phase == "scan":
            # The merge is done: the scan is over and the results are next.
            load.phase = "results"
            load.done, load.total = 0, len(event.get("models", ()))
        elif kind == "opened":
            error = event.get("error")
            if error is None:
                return
            if self.session.project is not None:
                # A project is open: this one, complete after a scoring
                # failure, or the previous one after a refused reopen.
                load.phase, load.error = "done", error
            else:
                load.phase, load.error = "failed", error

    # -- what the pages read -------------------------------------------------

    def placed(
        self, placement: Placement, view: SessionView
    ) -> list[tuple[Capability, bool | str]]:
        """Every registered capability that attaches at `placement`, with
        `available`'s answer for `view`, in registration order."""
        return [
            (cap, cap.available(view))
            for cap in registered_capabilities()
            if placement in cap.placements
        ]

    def view_for(self, selection: Selection) -> SessionView:
        return SessionView(self.session, selection)

    def visible_tests(self) -> list[str]:
        """The project's tests after the CLI's `--filter` / `--package`."""
        project = self.session.project
        if project is None:
            return []
        return [t.model_id for t in _filter_tests(project.tests, self.filter, self.package)]

    def busy_reason(self) -> str | None:
        """Why a Save, a Discard or a backend change is refused now, or None:
        the open thread is still writing, or a job holds models."""
        if self.load.in_progress:
            return "the project is still loading"
        running = self.session.running
        if running is not None:
            return f"{running.label} is running"
        return None

    def stop_reasons(self) -> list[str]:
        """What Stop server would lose (brief §3.3): pending edits, a running
        job — sub-project 5 adds the queue."""
        reasons: list[str] = []
        n = len(self.session.spec.pending)
        if n:
            reasons.append(f"{plural(n, 'unsaved edit')} will be lost")
        running = self.session.running
        if running is not None:
            reasons.append(f"{running.label} is still running")
        return reasons

    def stop(self) -> None:
        """Stop server: the children first (decision 7), then NiceGUI."""
        self.terminate_children()
        app.shutdown()

    def terminate_children(self) -> None:
        for child in self.children:
            if child.poll() is None:
                child.terminate()

    # -- the pages ---------------------------------------------------------------

    def page_setup(self) -> None:
        """Every page starts here: the Quasar palette and the token
        stylesheet from `reporting.theme`, the shell's own on top — one token
        table for the dashboard, the report and the tool (roadmap §1
        predicate 2; the body font is this shell's to apply)."""
        ui.colors(**quasar_palette())
        ui.add_css(css_variables() + STYLE)

    def mount(self) -> None:
        """The routes beside the index: the dashboard's files, resolved
        against the OPEN project's work dir per request so a reopen follows,
        and the Test page. Once per process; `index` calls it too, so a
        simulation that mounted only the root page gets them on its first
        build."""
        if self._mounted:
            return
        self._mounted = True

        @app.get("/dashboard/{path:path}")
        def _dashboard(path: str) -> Any:
            project = self.session.project
            if project is None:
                return JSONResponse({"error": "no project open"}, status_code=409)
            work_dir = project.config.work_dir
            assert work_dir is not None  # resolved in Config.__post_init__
            target = (work_dir / path).resolve()
            if work_dir.resolve() not in target.parents or not target.is_file():
                return JSONResponse({"error": "not found"}, status_code=404)
            return FileResponse(target)

        @ui.page("/test/{model_id}")
        async def _test(model_id: str) -> None:
            from .test_page import TestPage

            self.start()
            await TestPage(self).build(unquote(model_id))

    async def index(self) -> None:
        """The Suite page at `/`: `ui.run(shell.index, ...)` mounts it, the
        tests `user_simulation(root=shell.index)`."""
        from .suite import SuitePage

        self.mount()
        self.start()
        await SuitePage(self).build()
```

(The `_test` route imports `test_page`, which Task 6 creates; until then the route raises on a visit and nothing visits it.)

- [ ] **Step 6: `header.py`.**

```python
"""The header both pages share (DESIGN.md "Header"; brief §3.3): "DSTF", the
library and the backend on the left; the run indicator in the centre; the
pending count with Save / Discard / Export, the dashboard link and Stop
server on the right. Every button's label says its object, effect and
count; a disabled one carries the sentence that says why (DESIGN.md
"Disabled states explain themselves"). Off-white with a hairline below,
never an accent bar. "Open in another backend…" joins in Task 8."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from nicegui import ui

from ...storage.ref_index import _atomic_write_text
from ..capabilities import plural
from ..session import StaleSpecError
from .widgets import Action

if TYPE_CHECKING:
    from .shell import Shell


class Header:
    def __init__(self, shell: Shell) -> None:
        self.shell = shell
        self.drawer = PendingDrawer(shell)
        self.dialog = ui.dialog()  # the header's dialogs, one element rebuilt per use
        with ui.header().classes("dstf-header items-center no-wrap gap-3"):
            ui.label("DSTF").classes("dstf-wordmark").mark("wordmark")
            self.library = ui.label().classes("text-secondary").mark("project")
            self.backend = (
                ui.select([], label="backend", on_change=self._change_backend)
                .props("dense outlined options-dense")
                .mark("backend")
            )
            self.backend_why = ui.label().classes("dstf-why").mark("backend:why")
            ui.space()
            self.runs = ui.label().classes("text-secondary").mark("runs")
            ui.space()
            self.pending = (
                ui.button(on_click=self.drawer.toggle)
                .props("flat dense no-caps")
                .mark("pending")
            )
            self.save = Action(
                self._save, props="unelevated dense no-caps color=primary", marker="save"
            )
            self.discard = Action(
                self._discard, props="flat dense no-caps", marker="discard", why=False
            )
            self.export = Action(
                self._export, props="flat dense no-caps", marker="export", why=False
            )
            ui.link("dashboard", "/dashboard/dashboard.html", new_tab=True)
            self.stop = (
                ui.button("Stop server", on_click=self._stop)
                .props("outline dense no-caps color=negative")
                .mark("stop")
            )
        self.refresh()

    def handle(self, event: dict[str, Any]) -> None:
        self.refresh()

    def refresh(self) -> None:
        shell, s = self.shell, self.shell.session
        project, load = s.project, shell.load
        if project is not None:
            self.library.set_text(project.config.library_name or "")
            if not load.in_progress:
                # Not while a reopen runs: the select shows the user's choice
                # until the Session commits it.
                options = sorted(set(project.config.backends) | {project.config.backend_name})
                self.backend.set_options(options, value=project.config.backend_name)
        else:
            self.library.set_text("opening…" if load.in_progress else "no project")
        why = self._backend_why()
        self.backend.set_enabled(why is None)
        self.backend_why.set_text(why or "")
        self.backend_why.set_visibility(why is not None)
        running = s.running
        self.runs.set_text(f"1 running · {running.label}" if running is not None else "idle")
        n = len(s.spec.pending)
        models = len({op.model_id for op in s.spec.pending})
        self.pending.set_text(plural(n, "pending edit"))
        reason = "no pending edits" if n == 0 else shell.busy_reason()
        self.save.set(
            f"Save {plural(n, 'spec edit')} ({plural(models, 'test')}) to {s.spec.path.name}",
            reason,
        )
        self.discard.set(f"Discard {plural(n, 'edit')}", reason)
        self.export.set(
            f"Export {plural(n, 'edit')} to spec_patch.json",
            "no pending edits" if n == 0 else None,
        )
        self.drawer.refresh()

    # -- the backend -------------------------------------------------------------

    def _backend_why(self) -> str | None:
        """Why the backend cannot change now (brief §3.6: refused while edits
        are pending or a run is in flight), or None."""
        s = self.shell.session
        if s.project is None:
            return "no project open"
        busy = self.shell.busy_reason()
        if busy is not None:
            return f"backend change waits: {busy}"
        n = len(s.spec.pending)
        if n:
            return f"backend change waits: save or discard {plural(n, 'pending edit')} first"
        return None

    def _change_backend(self, e: Any) -> None:
        """Reopen on the chosen backend (brief §3.6), on the shell's open
        thread; the page follows the loading events as on startup."""
        project = self.shell.session.project
        if project is None or e.value in (None, project.config.backend_name):
            return
        why = self._backend_why()
        if why is not None:
            ui.notify(why, type="warning")
            self.backend.set_value(project.config.backend_name)
            return
        self.shell.open_async(e.value)
        self.refresh()

    # -- the edits -----------------------------------------------------------------

    def _save(self) -> None:
        try:
            self.shell.session.save()
            ui.notify("saved")
        except StaleSpecError as e:
            self.dialog.clear()
            with self.dialog, ui.card():

                def reload() -> None:
                    self.shell.session.reload()
                    self.dialog.close()

                ui.label(f"{e.path} changed on disk since it was loaded.")
                with ui.row():
                    ui.button("Reload", on_click=reload).props("outline dense no-caps")
                    ui.button("Keep my edits", on_click=self.dialog.close).props(
                        "flat dense no-caps"
                    )
            self.dialog.open()
        except Exception as e:
            ui.notify(f"save failed: {e}", type="negative")

    def _discard(self) -> None:
        self.shell.session.discard()

    def _export(self) -> None:
        project = self.shell.session.project
        assert project is not None  # the Action is disabled without one
        work_dir = project.config.work_dir
        assert work_dir is not None  # resolved in Config.__post_init__
        path = work_dir / "spec_patch.json"
        _atomic_write_text(path, json.dumps(self.shell.session.export(), indent=2) + "\n")
        ui.notify(f"wrote {path}")

    # -- stop ----------------------------------------------------------------------

    def _stop(self) -> None:
        """Stop server (brief §3.3): at once with nothing to lose; else a
        confirm naming what would be lost."""
        reasons = self.shell.stop_reasons()
        if not reasons:
            self.shell.stop()
            return
        self.dialog.clear()
        with self.dialog, ui.card():
            ui.label("Stop the server?").classes("text-h6")
            for reason in reasons:
                ui.label(reason).mark("stop-reason")
            with ui.row():
                ui.button("Stop anyway", on_click=self.shell.stop).props(
                    "outline dense no-caps color=negative"
                ).mark("stop-anyway")
                ui.button("Keep running", on_click=self.dialog.close).props(
                    "flat dense no-caps"
                )
        self.dialog.open()


class PendingDrawer:
    """The pending-edit drawer (§4.7; brief §3.3 "the drawer listing them by
    test"): staged ops in readable form, grouped by model. Toggled by the
    header's pending button."""

    def __init__(self, shell: Shell) -> None:
        self.shell = shell
        self.element = ui.right_drawer(value=False).props("bordered")
        with self.element:
            self.body = ui.column().classes("w-full gap-1")

    def toggle(self) -> None:
        self.element.toggle()

    def refresh(self) -> None:
        self.body.clear()
        with self.body:
            groups = self.shell.session.spec.grouped()
            if not groups:
                ui.label("nothing staged").classes("text-secondary")
                return
            for model_id, ops in groups:
                ui.label(model_id).classes("text-bold dstf-mono").mark("drawer-model")
                for op in ops:
                    value = json.dumps(op.get("value")) if "value" in op else ""
                    ui.label(f"{op['op']} {op['path']} {value}"[:200]).classes(
                        "text-xs dstf-mono"
                    ).mark("drawer-op")
```

- [ ] **Step 7: `runs.py`** — the old footer's three methods, as a card:

```python
"""The runs card (§4.7 "the job panel"; brief §3.6 puts the queue panel on
the Suite page, and sub-project 5 replaces this card with it): the running
job's per-test progress from `status.json`, else the last job's outcome
and what its results say went wrong, then the store writes' lines and the
artifacts. Nothing here may raise — a malformed snapshot renders what it
has."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from nicegui import ui

from ...backends.progress import read_status
from ..session import RunningJob

if TYPE_CHECKING:
    from .shell import Shell


class RunsCard:
    def __init__(self, shell: Shell) -> None:
        self.shell = shell
        self.container = ui.column().classes("dstf-card w-full gap-1").mark("runs-card")
        self.refresh()

    def refresh(self) -> None:
        self.container.clear()
        with self.container:
            self._draw()

    def _draw(self) -> None:
        session = self.shell.session
        job = session.running
        last = session.last_job
        ui.label(f"running: {job.label}" if job else "no job running")
        if job is not None:
            self._progress_rows(job)
        elif last is not None:
            if last.error is None:
                ui.label(f"finished: {last.label}")
                # Only a job that reloaded the results owns them: after a
                # failure, or after a job that asked for no reload, the
                # outcome line stands alone rather than over the previous
                # run's messages.
                if last.reloaded:
                    self._result_rows(last)
            else:
                ui.label(
                    f"failed: {last.label} — {type(last.error).__name__}: {last.error}"
                ).classes("text-negative")
        for notice in session.notices:
            ui.label(notice).classes("text-xs")
        for artifact in session.artifacts:
            for path in artifact.paths:
                ui.link(f"{artifact.label}: {path.name}", path.as_uri(), new_tab=True)

    def _progress_rows(self, job: RunningJob) -> None:
        """One row per scoped test in the snapshot — but only a snapshot this
        job wrote: a ``start_wall`` older than the job's start belongs to the
        previous run (the same guard `dashboard_render` applies to a sidecar).
        Every shape here is whatever a file on disk held."""
        project = self.shell.session.project
        work_dir = project.config.work_dir if project is not None else None
        if work_dir is None:
            return
        status = read_status(work_dir)
        if status is None:
            return
        start_wall = status.get("start_wall")
        # `json.loads` accepts the literal `NaN`, and every comparison with a
        # NaN is False — so a non-finite value would slip past the staleness
        # test below. Non-finite is no timestamp: treat it as stale.
        if not isinstance(start_wall, (int, float)) or not math.isfinite(start_wall):
            start_wall = 0.0
        if start_wall < job.started_wall - 0.1:
            return
        rows = status.get("tests")
        if not isinstance(rows, list):
            rows = []
        for row in rows:
            model_id = row.get("model_id") if isinstance(row, dict) else None
            if not isinstance(model_id, str) or model_id not in job.models:
                continue
            parts = [model_id]
            for key in ("status", "phase"):
                if row.get(key):
                    parts.append(str(row[key]))
            elapsed = row.get("elapsed")
            if isinstance(elapsed, (int, float)):
                parts.append(f"{elapsed:.1f} s")
            if row.get("detail"):
                parts.append(str(row["detail"]))
            ui.label(" · ".join(parts)).classes("text-xs").mark("job-row")

    def _result_rows(self, last: RunningJob) -> None:
        """What the finished job left: the reloaded result's failure message
        per model it ran (§4.7), in project order. Only ever called for a job
        whose reload ran, so every row here is that job's own."""
        project = self.shell.session.project
        if project is None:
            return
        for test in project.tests:
            if test.model_id not in last.models:
                continue
            result = project.results.get(test.model_id)
            text = test.model_id
            if result is not None and not result.success:
                text = f"{text} · {result.error_message or 'simulation failed'}"
            ui.label(text).classes("text-xs").mark("job-row")
```

- [ ] **Step 8: `suite.py`** — this task's shape; Task 6 adds the list:

```python
"""The Suite page (brief §3.2 "two pages", §3.8; DESIGN.md "Tables"): what a
batch acts on. The header, the progress line, the test list with its
filters and checkboxes, the batch actions, the runs card. One object per
browser tab; its selection lives in the tab (`tabstate.py`), never in the
Session (D129)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nicegui import ui

from .header import Header
from .runs import RunsCard

if TYPE_CHECKING:
    from .shell import Shell


class SuitePage:
    def __init__(self, shell: Shell) -> None:
        self.shell = shell
        self.client: Any = None

    async def build(self) -> None:
        self.client = ui.context.client
        await self.client.connected()
        self.shell.page_setup()
        ui.page_title("DSTF")
        self.header = Header(self.shell)
        with ui.column().classes("dstf-suite w-full q-pa-md gap-3"):
            self.progress = ui.label().classes("dstf-progress").mark("progress")
            with ui.column().classes("gap-1") as self.hint:
                ui.label("No project is open. Start the tool on one:")
                ui.code("dstf author --config <path/to/testing.json>")
            self.body = ui.column().classes("w-full gap-3")  # the list (Task 6)
            self.runs = RunsCard(self.shell)
        self.shell.register(self)
        self.refresh()

    def refresh(self) -> None:
        load = self.shell.load
        text = load.text()
        self.progress.set_text(text)
        self.progress.set_visibility(bool(text))
        self.hint.set_visibility(self.shell.session.project is None and not load.in_progress)

    def handle(self, event: dict[str, Any]) -> None:
        self.header.handle(event)
        self.refresh()
        if event["event"] in ("job", "poll", "opened", "spec"):
            # `spec` too: an open's commit and a close reset `last_job`, the
            # notices and the artifacts, and the card must not keep showing
            # the previous project's.
            self.runs.refresh()
```

- [ ] **Step 9: `app.py` and the package export.**

```python
"""`dstf author` (§4.7 "Launch"; brief §3.8): the shell mounted, the project
opened on a thread once the server runs, served on 127.0.0.1 until Stop
server. The page appears before a result is read."""

from __future__ import annotations

from pathlib import Path

from nicegui import app, ui

from .shell import Shell


def run_app(
    config_path: Path,
    *,
    filter: str | None = None,
    package: str | None = None,
    port: int | None = None,
    work_dir: Path | None = None,
    backend: str | None = None,
    show: bool = True,
) -> None:
    shell = Shell(
        config_path, work_dir=work_dir, filter=filter, package=package, show_browser=show
    )
    shell.mount()

    def start() -> None:
        shell.start()
        shell.open_async(backend)

    app.on_startup(start)
    app.on_shutdown(shell.terminate_children)
    ui.run(
        shell.index,
        host="127.0.0.1",
        port=port or 8080,
        show=show,
        reload=False,
        title="DSTF",
    )
```

`src/dstf/authoring/shell/__init__.py` gains, after its docstring: `from .app import run_app` and `__all__ = ["run_app"]`. In `tests/test_cli.py::test_author_without_the_extra_says_how_to_install_it` the cached tuple becomes `("dstf.authoring.shell", "dstf.authoring.shell.app", "dstf.authoring.shell.shell", "dstf.authoring.forms")`.

- [ ] **Step 10: Run green.** `uv run --no-sync pytest -q tests/test_shell.py tests/test_cli.py tests/test_authoring_imports.py tests/test_config.py`. Expected: every test passes, the ten ported runs-card tests included. If `test_the_page_appears_while_the_project_opens…` waits out `_settled` at `"scoring results… 0 of 5"`, read `shell.load` in the failure: the `spec` event did not move the phase — check `_absorb`'s `spec` branch and that `Shell.__init__` subscribes to `spec`.

- [ ] **Step 11: Docs.** `docs/architecture.md`: in the tree, `│   ├── capabilities.py       # The capability contract: Capability, Scope, the five outcomes` → `… Capability, Placement, the five outcomes`, and the `app.py` line becomes

```
│   ├── shell/                # The NiceGUI tier: shell.py (the process: Session, open thread, event fan-out),
│   │                         #   suite.py + test_page.py (one object per browser tab), header.py, actions.py
│   │                         #   (a button per capability at a placement), runs.py, tabstate.py, figure_hook.py
```

and the paragraph below: "Only `app.py`, `forms.py` and the built-in panels import a UI framework" → "Only `shell/`, `forms.py` and the built-in panels import a UI framework (`tests/test_authoring_imports.py` holds the line)". `CLAUDE.md` line 32–33 (`authoring/` in Project Structure): `app.py (the NiceGUI shell)` → `shell/ (the NiceGUI tier: the process, the Suite and Test pages, the header, capabilities by placement)`.

- [ ] **Step 12: Gates, then commit** — `git add` the seven new files and `tests/test_shell.py` — with paths `src/dstf/config.py tests/test_config.py src/dstf/authoring/shell/__init__.py src/dstf/authoring/shell/shell.py src/dstf/authoring/shell/style.py src/dstf/authoring/shell/widgets.py src/dstf/authoring/shell/header.py src/dstf/authoring/shell/runs.py src/dstf/authoring/shell/suite.py src/dstf/authoring/shell/app.py tests/test_shell.py tests/test_cli.py tests/conftest.py docs/architecture.md CLAUDE.md` and message `shell: the process (Session, open thread, event fan-out, app timer), the header with captioned Save/Discard/Export and Stop server, the runs card, run_app`.

**Named mutations:**
- M5.1: `_absorb` sets `phase = "failed"` on every `opened` error, ignoring `project` → `test_a_partial_scoring_failure_opens_the_project_with_a_warning` fails.
- M5.2: `Header.refresh` sets `self.save` to the bare caption `"Save"` → `test_save_discard_and_export_say_their_object_effect_and_count` fails.
- M5.3: `_stop` calls `shell.stop()` without the confirm → `assert stopped == []` fails.
- M5.4: `stop_reasons` omits the running job → `"rerun is still running"` is not seen.
- M5.5: `Shell.__init__` subscribes to `loading` only, not `spec` → the progress line stays `scanning library…` while the read is gated: `test_the_page_appears_while_the_project_opens…` fails at `"scoring results… 0 of 5"`.
- M5.7: `Config.__post_init__` never sets `backends` → the config test fails (the header test's `options == ["Python"]` holds with an empty map too — `_project` names one backend; Task 8's reopen test reads the second entry).
- M5.6 (must survive, applied): `TICK = 0.5` → every test still passes (the `_settled` helpers wait up to 3 s); this shows the tests wait on state, not on timing.

**Done when:** `uv run --no-sync python -c "from dstf.authoring.shell import run_app"` succeeds; the tests of Step 1 and the ten ported ones pass; `dstf author --help` runs; the suite is green.

---

### Task 6: The test list, the per-tab state, the Test page stub

The row model (roadmap §2 "Settles"): rows are built once per test set and never resent — a check, a click, a filter keystroke or a rescore updates ONE element. Grouped by package with short names by default (brief §3.2), flat and full a toggle away; a text filter and status chips whose counts are the classifier's; "Check N shown tests" / "Uncheck N tests". Clicking a name opens the Test page in the same tab; the checked set lives in the tab (`app.storage.tab`) and is there when the user comes back (D129). The Test page is a stub: header, "← Suite", the model id and its badge, the sub-project 3 note — the TEST-placement buttons join it in Task 7.

**Files:**
- Create: `src/dstf/authoring/shell/tabstate.py`, `src/dstf/authoring/shell/test_page.py`
- Modify: `src/dstf/authoring/shell/suite.py`
- Create: `tests/test_suite_page.py`

- [ ] **Step 1: The tests, red.** Create `tests/test_suite_page.py`:

```python
"""The Suite page's list (brief §3.2, §3.8; DESIGN.md "Tables"): grouped or
flat, short or full; the filter and the status chips; checkboxes that
collect and never change the view, a click that opens the Test page and
checks nothing; the checked set kept in the tab across that navigation;
rows that fill in as results are scored. In NiceGUI's user simulation."""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

pytest.importorskip("nicegui")

from nicegui.testing.user_simulation import user_simulation  # noqa: E402

from dstf.authoring import capabilities as caps  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.shell.shell import Shell  # noqa: E402
from dstf.authoring.shell.suite import SuitePage  # noqa: E402
from dstf.reporting.status import count_by_status  # noqa: E402
from test_session import A, B, C, D, G, _project  # noqa: E402


@pytest.fixture(autouse=True)
def _registry():
    caps._reset_for_tests()
    register_builtins()
    yield
    caps._reset_for_tests()
    register_builtins()


@pytest.fixture
def shell(tmp_path):
    cfg, work = _project(tmp_path)
    s = Shell(cfg, work_dir=work)
    s.session.open(cfg, work_dir=work)
    s.load.phase = "done"
    return s


async def _settled(predicate, tries: int = 60) -> None:
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0.05)
    assert predicate()


def _one(user, marker: str):
    (element,) = user.find(marker=marker).elements
    return element


async def _open(user, path: str = "/") -> None:
    """Open the page AND wait for it to be built: every page awaits its
    client's handshake before drawing, and `user.open` returns as soon as
    the handshake is sent — the page resumes on the next loop iteration.
    The header's wordmark is the first element every page draws."""
    await user.open(path)
    await user.should_see(marker="wordmark", retries=20)


def _suite(shell, user) -> SuitePage:
    """The Suite page built for the user's CURRENT client: a page the user
    navigated away from stays registered (the simulation never deletes its
    client), so the shell may hold several."""
    (page,) = [
        p for p in shell.pages if isinstance(p, SuitePage) and p.client is user.client
    ]
    return page


def short(model_id: str) -> str:
    return model_id.rsplit(".", 1)[-1]


async def test_the_list_is_grouped_with_short_names_and_every_status(shell):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        assert _one(user, "group:FakeLib").text == "FakeLib"
        page = _suite(shell, user)
        for model_id, verdict in (
            (A, "FAIL"),
            (B, "NO_REF"),
            (C, "SIM_FAIL"),
            (D, "PASS"),
            (G, "PASS"),
        ):
            assert _one(user, model_id).text == short(model_id)
            assert page.rows[model_id].badge.text == verdict
            assert page.rows[model_id].verdict == verdict


async def test_flat_and_full_names_are_a_toggle_away(shell):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        _one(user, "naming").set_value(False)
        await _settled(lambda: _one(user, A).text == A)
        assert _suite(shell, user).state.short is False
        _one(user, "grouping").set_value(False)
        await user.should_not_see(marker="group:FakeLib", retries=20)
        assert [r for r in _suite(shell, user).rows] == [A, B, C, D, G]  # project order


async def test_a_click_opens_the_test_page_and_checks_nothing(shell):
    """Brief §3.2: viewing and selecting are separate."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _suite(shell, user)
        user.find(marker=A).click()
        await user.should_see("Plots, criteria and the Variables drawer", retries=20)
        assert _one(user, "model").text == A
        assert _one(user, "status").text == "FAIL"
        assert page.state.checked == set()


async def test_checks_collect_without_changing_the_view_and_survive_the_navigation(shell):
    """Brief §3.2: checkboxes only collect; selection state is kept per
    browser tab, so it is there when the Test page hands the tab back."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        first = _suite(shell, user)
        user.find(marker=f"check:{D}").click()
        user.find(marker=f"check:{A}").click()
        assert first.state.checked == {A, D}
        # A navigation from a handler is a background task that runs at the
        # next await: give it one, then assert the client did not move.
        await asyncio.sleep(0.2)
        assert user.client is first.client  # still the Suite page
        user.find(marker=A).click()
        await user.should_see("← Suite", retries=20)
        user.find("← Suite").click()
        await user.should_see("Check", retries=20)
        second = _suite(shell, user)  # the page built for the new client
        assert second is not first  # a new page object for the new build
        assert second.state is first.state  # the same tab's state
        assert _one(user, f"check:{D}").value is True
        assert _one(user, f"check:{A}").value is True
        assert _one(user, f"check:{B}").value is False
```

```python
async def test_the_filter_and_the_chips_narrow_the_list_without_resending_it(shell):
    """The row model: a filter hides rows, it does not rebuild them — the
    element ids are the ones the page was built with — and the Session is
    not consulted."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _suite(shell, user)
        ids = {m: row.container.id for m, row in page.rows.items()}
        comparisons = dict(shell.session.comparisons)
        _one(user, "needle").set_value("d")
        assert page.rows[D].container.visible and not page.rows[A].container.visible
        assert {m: row.container.id for m, row in page.rows.items()} == ids
        _one(user, "needle").set_value("")
        user.find(marker="chip:PASS").click()
        assert page.state.status_filter == {"PASS"}
        assert page.rows[D].container.visible and page.rows[G].container.visible
        assert not page.rows[A].container.visible and not page.rows[B].container.visible
        user.find(marker="chip:PASS").click()  # off again
        assert page.state.status_filter == set() and page.rows[A].container.visible
        assert {m: row.container.id for m, row in page.rows.items()} == ids
        assert shell.session.comparisons == comparisons


async def test_the_chips_carry_the_classifiers_counts(shell):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        counts = count_by_status(list(shell.session.comparisons.values()))
        for status in ("PASS", "FAIL", "SIM_FAIL", "NO_REF"):
            assert _one(user, f"chip:{status}").text == f"{status} {counts[status]}"


async def test_check_shown_and_uncheck_say_their_counts_and_explain_zero(shell):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _suite(shell, user)
        assert _one(user, "check-shown").text == "Check 5 shown tests"
        assert _one(user, "uncheck").text == "Uncheck 0 tests"
        assert not _one(user, "uncheck").enabled
        assert _one(user, "uncheck:why").text == "nothing is checked"
        _one(user, "needle").set_value("d")
        await _settled(lambda: _one(user, "check-shown").text == "Check 1 shown test")
        user.find(marker="check-shown").click()
        assert page.state.checked == {D}
        _one(user, "needle").set_value("")
        await _settled(lambda: _one(user, "uncheck").text == "Uncheck 1 test")
        assert _one(user, f"check:{D}").value is True
        user.find(marker="uncheck").click()
        assert page.state.checked == set()
        await _settled(lambda: _one(user, f"check:{D}").value is False)


def _gate_per_model(monkeypatch) -> dict[str, threading.Event]:
    """Each result is handed on when ITS gate is set, so rows can be seen
    filling in one at a time."""
    from dstf.backends.base import Backend

    real = Backend.read_last_results
    gates: dict[str, threading.Event] = {m: threading.Event() for m in (A, B, C, D, G)}

    def gated(self, tests, on_result=None):
        def slow(model_id, result):
            assert gates[model_id].wait(10)
            if on_result is not None:
                on_result(model_id, result)

        return real(self, tests, slow)

    monkeypatch.setattr(Backend, "read_last_results", gated)
    return gates


async def test_rows_fill_in_as_results_are_scored(tmp_path, monkeypatch):
    """Brief §3.8: the list fills in as tests are discovered and status
    badges as results are scored — per `loading` event, not at the end."""
    gates = _gate_per_model(monkeypatch)
    cfg, work = _project(tmp_path)
    shell = Shell(cfg, work_dir=work)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _suite(shell, user)
        shell.open_async()
        await _settled(lambda: A in page.rows)
        assert {m: page.rows[m].badge.text for m in (A, B, C, D, G)} == dict.fromkeys(
            (A, B, C, D, G), "…"
        )
        gates[A].set()
        await _settled(lambda: page.rows[A].badge.text == "FAIL")
        assert page.rows[D].badge.text == "…"  # still gated
        for gate in gates.values():
            gate.set()
        await _settled(lambda: shell.load.phase == "done")
        await _settled(lambda: page.rows[D].badge.text == "PASS")
        assert page.rows[C].badge.text == "SIM_FAIL"


async def test_a_model_whose_scoring_raised_reads_scoring_failed(tmp_path, monkeypatch):
    import dstf.authoring.session as session_mod

    real = session_mod.compare_one

    def failing(test, *args, **kwargs):
        if test.model_id == D:
            raise ValueError("boom")
        return real(test, *args, **kwargs)

    monkeypatch.setattr(session_mod, "compare_one", failing)
    cfg, work = _project(tmp_path)
    shell = Shell(cfg, work_dir=work)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.open_async()
        await _settled(lambda: shell.load.phase == "done")
        await _settled(lambda: _suite(shell, user).rows[D].badge.text == "scoring failed")
        assert _suite(shell, user).rows[A].badge.text == "FAIL"


async def test_a_changed_test_set_rebuilds_the_rows(shell):
    """A model the spec newly names (here by editing the file and reloading,
    the rescan's and Save's path) gets a row; the others keep theirs."""
    spec_path = shell.session.spec.path
    (spec_path.parent.parent / "Examples" / "E.py").write_text(
        "def simulate(stop_time, tolerance):\n    return {}\n", encoding="utf-8"
    )
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _suite(shell, user)
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec["tests"].append(
            {
                "model": "FakeLib.E",
                "source": "../Examples/E.py",
                "variables": ["x"],
                "simulation": {"stop_time": 1.0, "tolerance": 1e-6},
            }
        )
        spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        shell.session.reload()
        await _settled(lambda: "FakeLib.E" in page.rows)
        assert page.rows["FakeLib.E"].badge.text == "SIM_FAIL"  # no result
        assert set(page.rows) == {A, B, C, D, G, "FakeLib.E"}
```

Run `uv run --no-sync pytest -q tests/test_suite_page.py`. Expected: collection error `ImportError: cannot import name 'SuitePage'`? No — `SuitePage` exists since Task 5; the error is at the first test: `AssertionError` from `_one(user, "group:FakeLib")` (no element carries the marker). Every test in the file fails at its first list assertion.

- [ ] **Step 2: `tabstate.py`.**

```python
"""Selection state per browser tab (brief §3.2; D129): what the Suite page
has checked, its filter and its layout. Kept in NiceGUI's tab storage, so
it is there after navigating to a Test page and back within the tab, and a
second tab on the same server starts with its own. Volatile: gone when the
tab closes. Never the Session's — that is the decision."""

from __future__ import annotations

from dataclasses import dataclass, field

from nicegui import app

KEY = "dstf"


@dataclass
class TabState:
    checked: set[str] = field(default_factory=set)
    needle: str = ""
    status_filter: set[str] = field(default_factory=set)
    grouped: bool = True
    short: bool = True


def tab_state() -> TabState:
    """This tab's state, created on first use. Needs a connected client: a
    page awaits ``ui.context.client.connected()`` before calling this."""
    store = app.storage.tab
    state = store.get(KEY)
    if not isinstance(state, TabState):
        state = TabState()
        store[KEY] = state
    return state
```

- [ ] **Step 3: `suite.py` — the list.** Add the imports `from urllib.parse import quote`, `from ...reporting.status import PASS, FAIL, SIM_FAIL, NO_REF, classify_with_class, count_by_status`, `from ..capabilities import plural`, `from .tabstate import TabState, tab_state`, `from .widgets import Action`, and `from dataclasses import dataclass`. Add the row model above `SuitePage`:

```python
CHIP_ORDER = (PASS, FAIL, SIM_FAIL, NO_REF)


@dataclass
class Row:
    """One test's row: the elements a check, a click or a rescore updates in
    place — the whole list is never resent (roadmap §2 "the row model")."""

    model_id: str
    group: str
    container: ui.row
    checkbox: ui.checkbox
    name: ui.button
    badge: ui.badge
    verdict: str = ""  # the classifier's word, "" while unscored


def _package(model_id: str) -> str:
    return model_id.rpartition(".")[0]


def _name(model_id: str, short: bool) -> str:
    return model_id.rsplit(".", 1)[-1] if short else model_id
```

Replace `build` from `self.body = …` on, and add the methods:

```python
    async def build(self) -> None:
        self.client = ui.context.client
        await self.client.connected()
        self.state: TabState = tab_state()
        self.shell.page_setup()
        ui.page_title("DSTF")
        self.header = Header(self.shell)
        self.rows: dict[str, Row] = {}
        self.groups: dict[str, ui.label] = {}
        self.chips: dict[str, ui.button] = {}
        with ui.column().classes("dstf-suite w-full q-pa-md gap-3"):
            self.progress = ui.label().classes("dstf-progress").mark("progress")
            with ui.column().classes("gap-1") as self.hint:
                ui.label("No project is open. Start the tool on one:")
                ui.code("dstf author --config <path/to/testing.json>")
            with ui.row().classes("items-center no-wrap gap-3 w-full"):
                self.needle = (
                    ui.input("filter", value=self.state.needle, on_change=self._on_needle)
                    .props("dense outlined clearable")
                    .mark("needle")
                )
                self.grouping = (
                    ui.toggle(
                        {True: "grouped", False: "flat"},
                        value=self.state.grouped,
                        on_change=self._on_layout,
                    )
                    .props("dense no-caps")
                    .mark("grouping")
                )
                self.naming = (
                    ui.toggle(
                        {True: "short names", False: "full names"},
                        value=self.state.short,
                        on_change=self._on_layout,
                    )
                    .props("dense no-caps")
                    .mark("naming")
                )
                with ui.row().classes("items-center gap-1").mark("counts"):
                    for status in CHIP_ORDER:
                        self.chips[status] = (
                            ui.button(on_click=lambda s=status: self._toggle_chip(s))
                            .props("flat dense no-caps")
                            .classes("dstf-chip")
                            .mark(f"chip:{status}")
                        )
                ui.space()
                self.check_shown = Action(
                    self._check_shown, props="flat dense no-caps", marker="check-shown", why=False
                )
                self.uncheck = Action(self._uncheck, props="flat dense no-caps", marker="uncheck")
            self.list = ui.column().classes("dstf-tests w-full gap-0").mark("tests")
            self.runs = RunsCard(self.shell)
        self.shell.register(self)
        self.rebuild()

    # -- the rows ---------------------------------------------------------------

    def rebuild(self) -> None:
        """The rows from scratch: on the first build, on a changed test set
        (a `spec` event), on a layout toggle. A check, a click, a filter or a
        rescore never comes here — each updates one element."""
        self.list.clear()
        self.rows, self.groups = {}, {}
        ids = self.shell.visible_tests()
        position = {m: i for i, m in enumerate(ids)}
        order = (
            sorted(ids, key=lambda m: (_package(m), position[m])) if self.state.grouped else ids
        )
        with self.list:
            current: str | None = None
            for model_id in order:
                group = _package(model_id)
                if self.state.grouped and group != current:
                    current = group
                    self.groups[group] = (
                        ui.label(group or "(top level)").classes("dstf-group").mark(f"group:{group}")
                    )
                with ui.row().classes("dstf-row items-center no-wrap gap-2 w-full") as container:
                    checkbox = (
                        ui.checkbox(
                            value=model_id in self.state.checked,
                            on_change=lambda e, m=model_id: self._toggle(m, bool(e.value)),
                        )
                        .props("dense")
                        .mark(f"check:{model_id}")
                    )
                    name = (
                        ui.button(
                            _name(model_id, self.state.short),
                            on_click=lambda m=model_id: self._open(m),
                        )
                        .props("flat dense no-caps align=left")
                        .classes("dstf-name")
                        .mark(model_id)
                    )
                    badge = ui.badge("").classes("dstf-badge")
                self.rows[model_id] = Row(model_id, group, container, checkbox, name, badge)
                self._paint(model_id)
        self._apply_filter()
        self.refresh()

    def _paint(self, model_id: str) -> None:
        """One row's verdict: the badge's word and fill, the row's tint.
        `…` while unscored; `scoring failed` for a model the open announced
        with `error=True` (Task 1)."""
        row = self.rows.get(model_id)
        if row is None:
            return
        comp = self.shell.session.comparisons.get(model_id)
        if comp is not None:
            verdict, css = classify_with_class(comp)
        elif model_id in self.shell.load.failed_models:
            verdict, css = "scoring failed", "sim-fail"
        else:
            verdict, css = "", "queued"
        row.verdict = verdict
        row.badge.set_text(verdict or "…")
        row.badge.style(f"background-color: var(--dstf-{css}-fill)")
        row.container.classes(
            replace=f"dstf-row items-center no-wrap gap-2 w-full dstf-tint-{css}"
        )

    def _apply_filter(self) -> None:
        """Visibility only — the rows stay."""
        needle = self.state.needle.lower()
        wanted = self.state.status_filter
        shown_by_group: dict[str, bool] = {}
        for row in self.rows.values():
            shown = (not needle or needle in row.model_id.lower()) and (
                not wanted or row.verdict in wanted
            )
            row.container.set_visibility(shown)
            shown_by_group[row.group] = shown_by_group.get(row.group, False) or shown
        for group, label in self.groups.items():
            label.set_visibility(shown_by_group.get(group, False))

    def _shown(self) -> list[str]:
        return [m for m, row in self.rows.items() if row.container.visible]

    def refresh(self) -> None:
        load = self.shell.load
        text = load.text()
        self.progress.set_text(text)
        self.progress.set_visibility(bool(text))
        self.hint.set_visibility(self.shell.session.project is None and not load.in_progress)
        # A copy: the open thread inserts into `comparisons` while this loop
        # runs on the UI loop, and iterating a dict another thread grows
        # raises. `dict.copy` is one C call under the GIL.
        comparisons = self.shell.session.comparisons.copy()
        counts = count_by_status([c for m, c in comparisons.items() if m in self.rows])
        for status, chip in self.chips.items():
            chip.set_text(f"{status} {counts[status]}")
            chip.classes(
                replace="dstf-chip"
                + (" dstf-chip-active" if status in self.state.status_filter else "")
            )
        shown = self._shown()
        self.check_shown.set(
            f"Check {plural(len(shown), 'shown test')}",
            None if shown else "nothing is shown",
        )
        checked = len(self.state.checked & set(self.rows))
        self.uncheck.set(
            f"Uncheck {plural(checked, 'test')}", None if checked else "nothing is checked"
        )

    # -- the user's gestures ------------------------------------------------------

    def _toggle(self, model_id: str, on: bool) -> None:
        (self.state.checked.add if on else self.state.checked.discard)(model_id)
        self.refresh()

    def _open(self, model_id: str) -> None:
        """A click views (brief §3.2): the Test page, in this tab."""
        ui.navigate.to(f"/test/{quote(model_id, safe='')}")

    def _on_needle(self, e: Any) -> None:
        self.state.needle = e.value or ""
        self._apply_filter()
        self.refresh()

    def _toggle_chip(self, status: str) -> None:
        wanted = self.state.status_filter
        (wanted.discard if status in wanted else wanted.add)(status)
        self._apply_filter()
        self.refresh()

    def _on_layout(self, e: Any) -> None:
        self.state.grouped = bool(self.grouping.value)
        self.state.short = bool(self.naming.value)
        self.rebuild()

    def _check_shown(self) -> None:
        for model_id in self._shown():
            self.state.checked.add(model_id)
            self.rows[model_id].checkbox.set_value(True)
        self.refresh()

    def _uncheck(self) -> None:
        for model_id in list(self.state.checked):
            self.state.checked.discard(model_id)
            row = self.rows.get(model_id)
            if row is not None:
                row.checkbox.set_value(False)
        self.refresh()

    # -- the Session's events ----------------------------------------------------

    def handle(self, event: dict[str, Any]) -> None:
        """Every Session event, from the shell's tick. No early return: the
        card and the rows are independent regions, and a `spec` that rebuilds
        the rows (a close, an `OpenProject`, the rescan adding a model) is
        exactly the event after which the card must not show the previous
        project's outcome."""
        self.header.handle(event)
        kind = event["event"]
        if kind in ("job", "poll", "opened", "spec"):
            self.runs.refresh()  # `spec`: an open or a close resets the card's sources
        if kind == "spec":
            if set(self.rows) != set(self.shell.visible_tests()):
                self.rebuild()  # ends with `_apply_filter` and `refresh`; repeating them below is cheap
        elif kind == "loading":
            if event.get("phase") == "results":
                if not self.rows:
                    self.rebuild()  # a page opened before the spec event
                self._paint(str(event["model"]))
        elif kind == "results":
            for model_id in event.get("models", ()):
                self._paint(str(model_id))
        self._apply_filter()
        self.refresh()
```

`ui.checkbox.set_value(True)` in `_check_shown` fires `on_change` → `_toggle` adds again (a set: idempotent); the explicit `add` keeps the state right even if a future NiceGUI stops firing on programmatic sets.

- [ ] **Step 4: `test_page.py`.**

```python
"""The Test page (brief §3.2): one test, its variables and its editing —
which arrive with sub-project 3. Until then a stub: the header, the way
back, the model and its verdict, and the TEST-placement buttons (Task 7).
One object per browser tab, like the Suite page."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nicegui import ui

from ...reporting.status import classify_with_class
from .header import Header
from .tabstate import tab_state

if TYPE_CHECKING:
    from .shell import Shell


class TestPage:
    def __init__(self, shell: Shell) -> None:
        self.shell = shell
        self.client: Any = None
        self.model_id = ""
        self.badge: ui.badge | None = None

    async def build(self, model_id: str) -> None:
        self.client = ui.context.client
        await self.client.connected()
        self.state = tab_state()  # the tab's, shared with its Suite page
        self.shell.page_setup()
        self.model_id = model_id
        ui.page_title(f"DSTF — {model_id.rsplit('.', 1)[-1]}")
        self.header = Header(self.shell)
        with ui.column().classes("dstf-test w-full q-pa-md gap-3"):
            ui.link("← Suite", "/").mark("back")
            project = self.shell.session.project
            test = project.test(model_id) if project is not None else None
            if test is None:
                ui.label(f"{model_id} is not a test of this project").classes("text-negative")
                self.shell.register(self)
                return
            with ui.row().classes("items-center gap-3"):
                ui.label(model_id).classes("text-h6 dstf-mono").mark("model")
                self.badge = ui.badge("").classes("dstf-badge").mark("status")
            ui.label(
                "Plots, criteria and the Variables drawer arrive with the Test page "
                "(sub-project 3)."
            ).classes("text-secondary text-xs")
        self.shell.register(self)
        self._paint()

    def _paint(self) -> None:
        if self.badge is None:
            return
        comp = self.shell.session.comparisons.get(self.model_id)
        verdict, css = classify_with_class(comp) if comp is not None else ("", "queued")
        self.badge.set_text(verdict or "…")
        self.badge.style(f"background-color: var(--dstf-{css}-fill)")

    def handle(self, event: dict[str, Any]) -> None:
        self.header.handle(event)
        if event["event"] in ("results", "loading", "spec"):
            self._paint()
```

- [ ] **Step 5: Run green.** `uv run --no-sync pytest -q tests/test_suite_page.py tests/test_shell.py`. Expected: all pass. If `test_checks_collect…` fails at `second.state is first.state`, the tab storage was not shared: check that both pages call `tab_state()` AFTER `await self.client.connected()` and that the simulation's `user.open` sent a `tab_id` (it does; `User.tab_id` is fixed per `User`).

- [ ] **Step 6: Gates, then commit** — `git add src/dstf/authoring/shell/tabstate.py src/dstf/authoring/shell/test_page.py tests/test_suite_page.py` — paths `src/dstf/authoring/shell/tabstate.py src/dstf/authoring/shell/test_page.py src/dstf/authoring/shell/suite.py tests/test_suite_page.py`, message `suite page: the row model (built once, updated per element), grouped/flat and short/full, the filter and the classifier's chips, checks kept per tab across a click into the Test page stub`.

**Named mutations:**
- M6.1: `_open` also adds the model to `state.checked` → `test_a_click_opens_the_test_page_and_checks_nothing` fails.
- M6.2: `_toggle` also calls `ui.navigate.to(...)` → the checks test leaves the Suite page on its first checkbox click: after the `asyncio.sleep(0.2)`, `assert user.client is first.client` fails.
- M6.3: `tab_state()` returns a fresh `TabState()` every call → `second.state is first.state` fails and the checkbox values are False.
- M6.4: `handle` paints only on the batch `results` event, never on `loading(results)` → `test_rows_fill_in_as_results_are_scored` fails: A stays `…` while D is gated.
- M6.5: `_on_needle` calls `rebuild()` → the element ids change; `test_the_filter_and_the_chips_narrow_the_list_without_resending_it` fails.
- M6.6: `refresh` counts every comparison, not those with a row → not caught by the FakeLib project (every scored model has a row). Must survive; the reviewer applies it and reports survival — the count-over-rows rule is documented, not guarded, and a `--filter` gate over TRANSFORM (Task 11) reads the chips against the filtered set.

**Done when:** every test in `tests/test_suite_page.py` passes; a click on a row's name navigates and changes no checkbox; the checked set is the same object on the rebuilt page; the row containers keep their ids across a filter; the suite is green.

---

### Task 7: Buttons by placement; the conformance test

Decisions 3 and 4. `ActionBar`: one `Action` per registered capability whose `placements` names this placement, labelled by `caption(view)`, disabled with `available`'s sentence beside it — and with "batch actions unlock when loading finishes" while the open runs (brief §3.8). A click opens the bar's dialog with the capability's `panel`; the panel's `submit` runs `act` and hands the outcome to the Session; a successful act closes the dialog, a raising one shows the error in it and changes nothing. The Suite page carries the bar at SUITE over the checked set; the Test page at TEST over its one test.

**Files:**
- Create: `src/dstf/authoring/shell/actions.py`
- Modify: `src/dstf/authoring/shell/suite.py`, `src/dstf/authoring/shell/test_page.py`
- Create: `tests/test_capability_conformance.py`

- [ ] **Step 1: The conformance test, red.** Create `tests/test_capability_conformance.py`:

```python
"""The conformance test (§4.6 "Executable form", amended by D129): a
capability the shell has never heard of attaches at each placement — one
dummy per `Placement`, one per `Outcome` kind, and one registered through
the `dstf.plugins` hook instead of by this file's own call. The shell lists
it with its caption, greys it with the sentence `available` gave, and
routes what `act` returned to that outcome's effect and no other — with no
file outside `tmp_path` written and the repository untouched. SUITE and
TEST are drawn by the two pages this sub-project has; VARIABLE and
CRITERION are listed by the shell's `placed` query and drawn by the Test
page from sub-project 3 (owner call 2 of the plan)."""

from __future__ import annotations

import asyncio
import importlib.metadata
import subprocess
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402
from nicegui.testing.user_simulation import user_simulation  # noqa: E402

from dstf import plugins  # noqa: E402
from dstf.authoring import capabilities as caps  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.capabilities import (  # noqa: E402
    Artifact,
    Job,
    OpenProject,
    Patch,
    Placement,
    StoreWrite,
)
from dstf.authoring.session import Selection  # noqa: E402
from dstf.authoring.shell.shell import Shell  # noqa: E402
from dstf.authoring.shell.suite import SuitePage  # noqa: E402
from test_session import LOOSEN, A, B, _Gate, _project, _snapshot  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


# -- the repository is not a test fixture ---------------------------------


def _git_status() -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@pytest.fixture(scope="module")
def _repo_before() -> str:
    return _git_status()


@pytest.fixture(scope="module", autouse=True)
def _repo_untouched(_repo_before: str):
    """Every test here is read-only outside its ``tmp_path``. The status is
    read once before the module and once after (a `git status` on this
    tree costs a second and a half; per test it doubled the module)."""
    yield
    after = _git_status()
    moved = sorted(set(after.splitlines()) ^ set(_repo_before.splitlines()))
    assert after == _repo_before, f"the repository changed under the test: {moved}"


# -- the registry ---------------------------------------------------------


@pytest.fixture(autouse=True)
def _registry():
    caps._reset_for_tests()
    register_builtins()
    yield
    caps._reset_for_tests()
    register_builtins()


@pytest.fixture
def _plugin_hook():
    plugins._reset_for_tests()
    yield
    plugins._reset_for_tests()


def only(*capabilities: Any) -> None:
    """Register these and nothing else: the claim under test is about a
    capability the shell has never heard of, and a builtin's own button
    would make "a button is drawn" read on either of two elements."""
    caps._reset_for_tests()
    for cap in capabilities:
        caps.register_capability(cap)


@pytest.fixture
def shell(tmp_path):
    cfg, work = _project(tmp_path)
    s = Shell(cfg, work_dir=work)
    s.session.open(cfg, work_dir=work)
    s.load.phase = "done"
    return s


class _Dummy:
    """A capability the shell has never heard of. Its panel is one button,
    and the button submits — the whole route from a panel to the Session."""

    def __init__(
        self,
        name: str,
        placements: set[Placement],
        outcome: Any = None,
        answer: bool | str = True,
    ) -> None:
        self.name = name
        self.label = f"Dummy {name}"
        self.placements = frozenset(placements)
        self.outcome = outcome
        self.answer = answer
        self.inputs: list[dict] = []

    def available(self, s):
        return self.answer

    def caption(self, s):
        return f"Dummy {self.name} on {len(s.selection.tests)} tests"

    def panel(self, s, panel):
        ui.button(f"submit {self.name}", on_click=lambda: panel.submit(who=self.name))

    def act(self, s, **inputs):
        self.inputs.append(inputs)
        return self.outcome


async def _settled(predicate, tries: int = 60) -> None:
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0.05)
    assert predicate()


def _one(user, marker: str):
    (element,) = user.find(marker=marker).elements
    return element


async def _open(user, path: str = "/") -> None:
    """Open the page AND wait for it to be built: every page awaits its
    client's handshake before drawing, and `user.open` returns as soon as
    the handshake is sent — the page resumes on the next loop iteration.
    The header's wordmark is the first element every page draws."""
    await user.open(path)
    await user.should_see(marker="wordmark", retries=20)


def _suite(shell, user) -> SuitePage:
    """The Suite page built for the user's current client (a navigated-away
    page stays registered under the simulation)."""
    (page,) = [
        p for p in shell.pages if isinstance(p, SuitePage) and p.client is user.client
    ]
    return page


def _effects(shell: Shell, tmp_path: Path) -> dict:
    """Everything an outcome can reach, in one comparable dict: what a test
    asserts is the ONE key its outcome changed."""
    session = shell.session
    project = session.project
    return {
        "pending": [op.path for op in session.spec.pending],
        "running": session.running.label if session.running else None,
        "artifacts": [a.label for a in session.artifacts],
        "project": project.config.library_name if project else None,
        "files": _snapshot(tmp_path),
    }


def _changed(before: dict, after: dict) -> list[str]:
    return [key for key in before if before[key] != after[key]]


async def _check(user, model_id: str) -> None:
    user.find(marker=f"check:{model_id}").click()


async def _submit(user, cap: _Dummy) -> None:
    """The caption button opens the dialog; the panel's own button submits."""
    user.find(marker=f"action:{cap.name}").click()
    await user.should_see(f"submit {cap.name}", retries=20)
    user.find(f"submit {cap.name}").click()


# -- one dummy per Placement ------------------------------------------------


@pytest.mark.parametrize("placement", list(Placement), ids=lambda p: p.value)
def test_the_shell_lists_a_capability_at_its_placement_and_nowhere_else(shell, placement):
    cap = _Dummy(placement.value, {placement})
    only(cap)
    view = shell.view_for(Selection(tests=frozenset({A})))
    assert shell.placed(placement, view) == [(cap, True)]
    for other in Placement:
        if other is not placement:
            assert shell.placed(other, view) == []


async def test_the_suite_page_draws_a_suite_capability_with_its_caption(shell):
    suite_cap = _Dummy("suite", {Placement.SUITE})
    test_cap = _Dummy("test", {Placement.TEST})
    only(suite_cap, test_cap)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        button = _one(user, "action:suite")
        assert button.text == "Dummy suite on 0 tests" and button.enabled
        await user.should_not_see(marker="action:test")  # not this page's
        await _check(user, A)
        await _settled(lambda: button.text == "Dummy suite on 1 tests")


async def test_the_test_page_draws_a_test_capability_for_its_one_test(shell):
    suite_cap = _Dummy("suite", {Placement.SUITE})
    test_cap = _Dummy("test", {Placement.TEST})
    only(suite_cap, test_cap)
    async with user_simulation(root=shell.index) as user:
        shell.mount()  # the Test page's route, without visiting `/` first
        await _open(user, f"/test/{A}")
        button = _one(user, "action:test")
        assert button.text == "Dummy test on 1 tests" and button.enabled
        await user.should_not_see(marker="action:suite")


# -- greyed with its reason ------------------------------------------------


async def test_an_unavailable_capability_is_greyed_with_its_reason(shell):
    """`available` returning a string is the sentence beside the button;
    the panel is not built, so there is nothing to submit."""
    cap = _Dummy("accept", {Placement.SUITE}, answer="needs a baseline")
    only(cap)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        button = _one(user, "action:accept")
        assert not button.enabled and button.text == "Dummy accept on 0 tests"
        assert _one(user, "action:accept:why").text == "needs a baseline"
        with pytest.raises(AssertionError):  # `find` raises on absence
            user.find("submit accept")
        assert cap.inputs == []


async def test_batch_actions_are_greyed_while_the_project_loads(shell):
    """Brief §3.8: batch actions unlock when loading finishes."""
    cap = _Dummy("suite", {Placement.SUITE})
    only(cap)
    # The fixture's synchronous open left its events queued; the shell's
    # FIRST tick (`immediate=True`) replays them — scan, spec, results, done
    # — while the page waits for its handshake, and `_absorb` would reset the
    # phase this test sets. Drain them: this test is about the in-between.
    while not shell._events.empty():
        shell._events.get_nowait()
    shell.load.phase = "results"  # as during an open
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        button = _one(user, "action:suite")
        assert not button.enabled
        assert _one(user, "action:suite:why").text == "batch actions unlock when loading finishes"
        shell.load.phase = "done"
        shell._events.put({"event": "loading", "phase": "done"})
        await _settled(lambda: button.enabled)


# -- one dummy per Outcome kind ------------------------------------------


async def test_patch_reaches_the_pending_ops_and_nothing_else(shell, tmp_path):
    cap = _Dummy("patch", {Placement.SUITE}, outcome=Patch(ops=[LOOSEN]))
    only(cap)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        await _check(user, A)
        before = _effects(shell, tmp_path)
        await _submit(user, cap)
        await _settled(lambda: _one(user, "pending").text == "2 pending edits")
        after = _effects(shell, tmp_path)
    assert cap.inputs == [{"who": "patch"}] and _suite(shell, user).actions.errors == {}
    assert _changed(before, after) == ["pending"]
    assert after["pending"] == ["/metrics", LOOSEN.path]


async def test_store_write_runs_against_the_store_and_nothing_else(shell, tmp_path):
    seen: list[Any] = []
    cap = _Dummy(
        "accept",
        {Placement.SUITE},
        outcome=StoreWrite(run=seen.append, models=frozenset({B})),
    )
    only(cap)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        await _check(user, B)
        before = _effects(shell, tmp_path)
        await _submit(user, cap)
        await _settled(lambda: seen)
        after = _effects(shell, tmp_path)
    assert seen == [shell.session.project.store] and _suite(shell, user).actions.errors == {}
    assert _changed(before, after) == []  # the write is the whole effect


async def test_job_runs_on_a_thread_and_the_runs_card_says_so(shell, tmp_path):
    gate = _Gate()
    cap = _Dummy(
        "rerun",
        {Placement.SUITE},
        outcome=Job(run=gate, label="conformance rerun", models=frozenset({A})),
    )
    only(cap)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        await user.should_see("no job running")
        await _check(user, A)
        before = _effects(shell, tmp_path)
        await _submit(user, cap)
        await user.should_see("running: conformance rerun", retries=20)
        assert _changed(before, _effects(shell, tmp_path)) == ["running"]
        gate.release.set()
        await user.should_see("no job running", retries=50)
        after = _effects(shell, tmp_path)
    assert gate.seen is not None and gate.seen.models == frozenset({A})
    assert _suite(shell, user).actions.errors == {}
    assert _changed(before, after) == []


async def test_artifact_is_shown_as_a_link_and_nothing_else(shell, tmp_path):
    png = tmp_path / "figure.png"
    png.write_bytes(b"\x89PNG")
    cap = _Dummy(
        "export",
        {Placement.SUITE},
        outcome=Artifact(paths=[png], label="Conformance PNG"),
    )
    only(cap)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        before = _effects(shell, tmp_path)
        await _submit(user, cap)
        await user.should_see("Conformance PNG", retries=20)
        (link,) = user.find(kind=ui.link, content="Conformance PNG").elements
        assert link.props["href"] == png.as_uri()
        after = _effects(shell, tmp_path)
    assert _suite(shell, user).actions.errors == {}
    assert _changed(before, after) == ["artifacts"]
    assert after["artifacts"] == ["Conformance PNG"]


async def test_open_project_swaps_the_project_in_the_header(shell, tmp_path):
    other_cfg, other_work = _project(tmp_path / "other")
    other_cfg.write_text(
        other_cfg.read_text(encoding="utf-8").replace("FakeLib", "OtherLib", 1),
        encoding="utf-8",
    )
    cap = _Dummy(
        "recent",
        {Placement.SUITE},
        outcome=OpenProject(config_path=other_cfg, work_dir=other_work),
    )
    only(cap)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        assert _one(user, "project").text == "FakeLib"
        before = _effects(shell, tmp_path)
        await _submit(user, cap)
        await _settled(lambda: _one(user, "project").text == "OtherLib")
        after = _effects(shell, tmp_path)
    assert _suite(shell, user).actions.errors == {}
    assert _changed(before, after) == ["project"]
    assert shell.session.project.config.config_file == other_cfg


async def test_an_act_that_raises_shows_in_the_dialog_and_changes_nothing(shell, tmp_path):
    class _Raising(_Dummy):
        def act(self, s, **inputs):
            raise ValueError("nope")

    cap = _Raising("bad", {Placement.SUITE})
    only(cap)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        before = _effects(shell, tmp_path)
        await _submit(user, cap)
        await user.should_see("ValueError: nope", retries=20)
        assert _suite(shell, user).actions.errors == {"bad": "ValueError: nope"}
        assert _one(user, "error:bad").visible
        assert _changed(before, _effects(shell, tmp_path)) == []


# -- through the plug-in hook --------------------------------------------


async def test_a_capability_registered_through_the_plugin_hook_attaches(
    shell, tmp_path, monkeypatch, _plugin_hook
):
    """Nothing here calls `register_capability`: the entry point's load()
    returns the callable that does, and `load_plugins` invokes it."""
    cap = _Dummy("from-plugin", {Placement.SUITE}, outcome=Patch(ops=[LOOSEN]))

    class _EP:
        name = "fake"

        def load(self):
            return lambda: caps.register_capability(cap)

    def fake_entry_points(*, group=None):
        return [_EP()] if group == plugins.PLUGIN_GROUP else []

    monkeypatch.setattr(importlib.metadata, "entry_points", fake_entry_points)
    caps._reset_for_tests()
    plugins.load_plugins()
    assert caps.registered_capabilities() == (cap,)

    async with user_simulation(root=shell.index) as user:
        await _open(user)
        await _check(user, A)
        before = _effects(shell, tmp_path)
        await _submit(user, cap)
        await _settled(lambda: _one(user, "pending").text == "2 pending edits")
        after = _effects(shell, tmp_path)
    assert _suite(shell, user).actions.errors == {}
    assert _changed(before, after) == ["pending"]
```

Run `uv run --no-sync pytest -q tests/test_capability_conformance.py`. Expected: the four `placed` tests PASS already (Task 4's contract and Task 5's `placed`); every page test fails — `AssertionError` at `_one(user, "action:…")` (no bar exists) or, for the panel tests, at `_submit`.

- [ ] **Step 2: `actions.py`.**

```python
"""Capabilities at a placement (brief §3.2; DESIGN.md "Buttons", "Disabled
states explain themselves"): one outlined button per registered capability
whose `placements` names this one, labelled by the capability's `caption`,
disabled with the sentence `available` gave beside it; a click opens the
bar's dialog hosting the capability's `panel`, whose `submit` runs `act`
and hands the outcome to the Session. The bar knows no capability by name.
The dialog is not rebuilt while open (decision 4): a confirm list belongs
to the moment of the click; a successful act closes it, a raising one
shows the error in it and changes nothing."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from nicegui import ui

from ..capabilities import Capability, Placement
from ..capabilities import FigureHook as FigureHookProtocol
from ..session import Selection
from .widgets import Action

if TYPE_CHECKING:
    from .shell import Shell

LOADING = "batch actions unlock when loading finishes"


class _NoFigure:
    """`Panel.figure` where there is no plot (SUITE, TEST): a subscription
    here is a contract misuse, and loud (decision 3)."""

    def on(self, kind: str, handler: Callable[[str, dict], None]) -> None:
        raise RuntimeError(f"no figure at this placement to subscribe {kind!r} to")


class ShellPanel:
    """The container a capability owns (§4.6 "Panel"): the dialog's slot,
    the figure hook, and `submit` — the one way an outcome reaches the
    Session."""

    figure: FigureHookProtocol

    def __init__(self, submit: Callable[..., None], figure: FigureHookProtocol) -> None:
        self._submit, self.figure = submit, figure

    def submit(self, **inputs: Any) -> None:
        self._submit(**inputs)


class ActionBar:
    def __init__(
        self,
        shell: Shell,
        placement: Placement,
        selection_of: Callable[[], Selection],
        *,
        marker: str,
    ) -> None:
        self.shell, self.placement, self.selection_of = shell, placement, selection_of
        self.errors: dict[str, str] = {}  # capability name -> the error its dialog shows
        self.actions: dict[str, Action] = {}
        self.dialog = ui.dialog()  # one element, rebuilt per open
        self.container = ui.row().classes("dstf-actions items-center gap-3 wrap").mark(marker)
        self.refresh()

    def refresh(self) -> None:
        """Captions and availability from the current selection; the buttons
        are rebuilt only when the set of capabilities placed here changed."""
        view = self.shell.view_for(self.selection_of())
        placed = self.shell.placed(self.placement, view)
        if [cap.name for cap, _ in placed] != list(self.actions):
            self.container.clear()
            self.actions = {}
            with self.container:
                for cap, _ in placed:
                    self.actions[cap.name] = Action(
                        lambda c=cap: self.open(c),
                        props="outline dense no-caps",
                        marker=f"action:{cap.name}",
                    )
        loading = self.shell.load.in_progress
        for cap, avail in placed:
            if loading:
                reason: str | None = LOADING
            elif avail is True:
                reason = None
            else:
                reason = avail if isinstance(avail, str) else "not available"
            self.actions[cap.name].set(cap.caption(view), reason)

    def open(self, cap: Capability) -> None:
        """The capability's panel, in the dialog, over the selection as it is
        at this click."""
        view = self.shell.view_for(self.selection_of())
        self.dialog.clear()
        with self.dialog, ui.card().classes("dstf-dialog"):
            ui.label(cap.label).classes("text-h6")
            error = ui.label(self.errors.get(cap.name, "")).classes("text-negative")
            error.mark(f"error:{cap.name}")
            error.set_visibility(bool(error.text))
            slot = ui.column().classes("w-full").mark(f"panel:{cap.name}")

            def submit(**inputs: Any) -> None:
                try:
                    self.shell.session.apply(cap.act(view, **inputs))
                except Exception as e:  # shown here; a raising act changes nothing
                    self.errors[cap.name] = f"{type(e).__name__}: {e}"
                    error.set_text(self.errors[cap.name])
                    error.set_visibility(True)
                    return
                self.errors.pop(cap.name, None)
                self.dialog.close()

            with slot:
                try:
                    cap.panel(view, ShellPanel(submit, _NoFigure()))
                except Exception as e:  # shown in the slot, never breaks the page
                    ui.label(f"{type(e).__name__}: {e}").classes("text-negative")
            ui.button("Close", on_click=self.dialog.close).props("flat dense no-caps").mark(
                f"close:{cap.name}"
            )
        self.dialog.open()
```

- [ ] **Step 3: The bars on the pages.** `suite.py`: import `from ..capabilities import Placement`, `from ..session import Selection`, `from .actions import ActionBar`; in `build`, between the toolbar row and `self.list = …`:

```python
            self.actions = ActionBar(
                self.shell,
                Placement.SUITE,
                lambda: Selection(tests=frozenset(self.state.checked)),
                marker="suite-actions",
            )
```

and `self.actions.refresh()` at the end of `_toggle`, `_check_shown` and `_uncheck` (the selection changed). `handle` becomes, in full — the bar refreshes on `results`, `job`, `spec`, `opened` and `loading(done)`, NOT on every `loading(results)`: Accept's `available` and `caption` each read `batch_manifest.json` through `_rows`, and 331 announcements times four reads is a disk scan for nothing:

```python
    def handle(self, event: dict[str, Any]) -> None:
        self.header.handle(event)
        kind = event["event"]
        if kind in ("job", "poll", "opened", "spec"):
            self.runs.refresh()
        if kind in ("results", "job", "spec", "opened") or (
            kind == "loading" and event.get("phase") == "done"
        ):
            self.actions.refresh()
        if kind == "spec":
            if set(self.rows) != set(self.shell.visible_tests()):
                self.rebuild()
        elif kind == "loading":
            if event.get("phase") == "results":
                if not self.rows:
                    self.rebuild()
                self._paint(str(event["model"]))
        elif kind == "results":
            for model_id in event.get("models", ()):
                self._paint(str(model_id))
        self._apply_filter()
        self.refresh()
```

(The docstring of Task 6's version stays.) `test_page.py`: the same imports; in `build`, after the model/badge row and before the note: `self.actions = ActionBar(self.shell, Placement.TEST, lambda: Selection(tests=frozenset({model_id})), marker="test-actions")`; `self.actions: ActionBar | None = None` in `__init__`; in `handle`, on EVERY event and outside the `_paint` condition — a `job` event must re-grey Rerun and Accept while a run holds the model — `if self.actions is not None: self.actions.refresh()`.

- [ ] **Step 4: Run green.** `uv run --no-sync pytest -q tests/test_capability_conformance.py tests/test_suite_page.py tests/test_shell.py tests/test_builtin_capabilities.py`. Expected: all pass. The Suite page now shows two builtin buttons — `Accept 0 results as baselines` and `Rerun 0 selected tests (Python)`, greyed with "Accept needs a selection" / "Rerun needs a selection" — which no Suite test asserted against; check none of `tests/test_suite_page.py`'s `should_see` calls became ambiguous.

- [ ] **Step 5: Gates, then commit** — `git add src/dstf/authoring/shell/actions.py tests/test_capability_conformance.py` — paths `src/dstf/authoring/shell/actions.py src/dstf/authoring/shell/suite.py src/dstf/authoring/shell/test_page.py tests/test_capability_conformance.py`, message `shell: capabilities attach by placement — a captioned button per capability, its panel in a dialog; the conformance test over the four placements and the five outcomes`.

**Named mutations:**
- M7.1: `ActionBar.refresh` lists every registered capability regardless of `placements` → `test_the_suite_page_draws_a_suite_capability…` fails at `not user.find(marker="action:test").elements`.
- M7.2: `submit` calls `cap.act` and drops the outcome → `test_patch_reaches_the_pending_ops…` fails.
- M7.3: `submit` swallows the exception without recording it (no `errors[...]`, dialog closed) → `test_an_act_that_raises…` fails.
- M7.4: the `LOADING` gate removed from `refresh` → `test_batch_actions_are_greyed_while_the_project_loads` fails.
- M7.5: `open` builds the panel for `avail is not True` too (no guard exists — the button is disabled instead) → NOT a mutation of this code; instead: `Action.set` enables the button regardless of `reason` → `test_an_unavailable_capability_is_greyed…` fails at `not button.enabled`.

**Done when:** every test of Step 1 passes; the Suite and Test pages each carry a bar whose buttons are the capabilities placed there and no other; the suite is green.

---

### Task 8: The backend: change in place, or open in another process

Brief §3.6, decisions 6 and 7. The header's select (built in Task 5, with `Config.backends` as its candidates) reopens the project on the chosen backend through the shell's open thread — the page follows the loading events as on startup — refused with its sentence while edits are pending, a job runs or the project is still loading; a backend that cannot be built leaves the project as it was and says why. This task adds the tests for that and "Open in another backend…": a second `dstf author` on a free port from the same config; the child opens its own browser tab (so no popup for the browser to block), the dialog shows its URL, and Stop server terminates it.

**Files:**
- Modify: `src/dstf/authoring/shell/shell.py` (`spawn`, `_free_port`), `src/dstf/authoring/shell/header.py` (the button and its dialog)
- Test: `tests/test_shell.py`

- [ ] **Step 1: The tests, red.** Append to `tests/test_shell.py`, under `# -- the backend --`:

```python
import subprocess  # noqa: E402  (with the other imports at the top of the file)
import sys  # noqa: E402

from dstf.reporting.status import FAIL, classify  # noqa: E402


def _with_alt_backend(cfg) -> None:
    """A second entry in the `backends` map that resolves to the same family:
    `Config.backend` maps "Python (alt)" to "Python", so the reopen is
    observable — a new Config, the select's new value — without a second
    simulator on this machine."""
    data = json.loads(cfg.read_text(encoding="utf-8"))
    data["backends"]["Python (alt)"] = ["python3", "python"]
    cfg.write_text(json.dumps(data), encoding="utf-8")


async def test_changing_the_backend_reopens_the_project_on_it(tmp_path):
    cfg, work = _project(tmp_path)
    _with_alt_backend(cfg)
    shell = Shell(cfg, work_dir=work)
    shell.session.open(cfg, work_dir=work)
    shell.load.phase = "done"
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        backend = _one(user, "backend")
        assert backend.options == ["Python", "Python (alt)"]
        backend.set_value("Python (alt)")
        await _settled(lambda: shell.load.phase == "done" and not shell.load.in_progress)
        await _settled(lambda: shell.session.project.config.backend_name == "Python (alt)")
        await _settled(lambda: backend.value == "Python (alt)")
        assert shell.load.error is None
        # The same family reads the same partition: the verdicts are the old ones.
        assert classify(shell.session.comparisons[A]) == FAIL


async def test_the_backend_select_is_greyed_with_its_sentence_while_edits_are_pending(shell):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        assert _one(user, "backend").enabled
        shell.session.stage([LOOSEN])
        await _settled(lambda: not _one(user, "backend").enabled)
        assert (
            _one(user, "backend:why").text
            == "backend change waits: save or discard 2 pending edits first"
        )
        shell.session.discard()
        await _settled(lambda: _one(user, "backend").enabled)


async def test_a_backend_that_cannot_be_built_leaves_the_project_and_says_why(shell):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.open_async("NoSuchBackend")
        await _settled(lambda: shell.load.phase == "done" and shell.load.error is not None)
        assert shell.session.project.config.backend_name == "Python"  # as it was
        assert _one(user, "backend").value == "Python"
        await user.should_see("warning:", retries=20)
        await user.should_see("NoSuchBackend")


class _FakeProc:
    def __init__(self, cmd, calls):
        self.cmd, self.calls = cmd, calls
        calls.append(cmd)

    def poll(self):
        return None

    def terminate(self):
        self.calls.append("terminated")


async def test_open_in_another_backend_starts_a_second_process_on_a_free_port(
    tmp_path, monkeypatch
):
    """Brief §3.6: one project and backend per process. The child gets this
    process's config, the chosen backend, a free port, and its `--work-dir`
    (decision 6); it opens its own browser tab; Stop server ends it
    (decision 7)."""
    cfg, work = _project(tmp_path)
    _with_alt_backend(cfg)
    shell = Shell(cfg, work_dir=work)
    shell.session.open(cfg, work_dir=work)
    shell.load.phase = "done"
    calls: list = []
    import dstf.authoring.shell.shell as shell_mod

    monkeypatch.setattr(shell_mod.subprocess, "Popen", lambda cmd: _FakeProc(cmd, calls))
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        user.find(marker="other-backend").click()
        await user.should_see("Open this project in another backend")
        _one(user, "other-choice").set_value("Python (alt)")
        user.find(marker="start-other").click()
        await user.should_see("open http://127.0.0.1:", retries=20)
    cmd = calls[0]
    assert cmd[:3] == [sys.executable, "-m", "dstf"]
    assert cmd[cmd.index("--config") + 1] == str(cfg)
    assert cmd[cmd.index("author") + 1 :][:2] == ["--backend", "Python (alt)"]
    assert cmd[cmd.index("--work-dir") + 1] == str(work)
    port = int(cmd[cmd.index("--port") + 1])
    assert 1024 < port < 65536
    assert "--no-browser" not in cmd  # the child opens its own tab
    shell.terminate_children()
    assert calls[-1] == "terminated"


def test_a_no_browser_parent_starts_no_browser_children_without_a_work_dir(
    tmp_path, monkeypatch
):
    cfg, _work = _project(tmp_path)
    calls: list = []
    import dstf.authoring.shell.shell as shell_mod

    monkeypatch.setattr(shell_mod.subprocess, "Popen", lambda cmd: _FakeProc(cmd, calls))
    shell = Shell(cfg, show_browser=False, filter="FakeLib.A*", package="FakeLib")
    url = shell.spawn("Python")
    cmd = calls[0]
    assert url.startswith("http://127.0.0.1:") and url.endswith("/")
    assert "--no-browser" in cmd and "--work-dir" not in cmd
    assert cmd[cmd.index("--filter") + 1] == "FakeLib.A*"
    assert cmd[cmd.index("--package") + 1] == "FakeLib"
```

Run `uv run --no-sync pytest -q tests/test_shell.py -k "changing or greyed_with_its_sentence or cannot_be_built or another_backend or no_browser"`. Expected: `test_changing_the_backend_reopens_the_project_on_it` and `test_a_backend_that_cannot_be_built…` PASS already (Task 5 built the select and the reopen; they are regression guards for this task's mutations M8.2 and M8.4); `test_the_backend_select_is_greyed…` PASSES too (Task 5's `_backend_why`); `AssertionError` at `user.find(marker="other-backend")` for the spawn test; `AttributeError: 'Shell' object has no attribute 'spawn'` for the no-browser test.

- [ ] **Step 2: `Shell.spawn`.** In `shell.py`, `import socket` and `import sys` join the imports; add, after `terminate_children`:

```python
    def spawn(self, backend: str) -> str:
        """`Open in another backend…` (brief §3.6): a second `dstf author` on
        `backend`, on a free port, from the same config — with this
        process's `--work-dir`, `--filter` and `--package` when it got them
        (decision 6: `--work-dir` means "this work dir"). The child opens its
        own browser tab unless this process was started with `--no-browser`,
        so no popup for the browser to block; the URL is returned for the
        dialog's link. `terminate_children` ends what this started
        (decision 7)."""
        port = _free_port()
        cmd = [
            sys.executable, "-m", "dstf", "--config", str(self.config_path),
            "author", "--backend", backend, "--port", str(port),
        ]
        if self.work_dir is not None:
            cmd += ["--work-dir", str(self.work_dir)]
        if self.filter:
            cmd += ["--filter", self.filter]
        if self.package:
            cmd += ["--package", self.package]
        if not self.show_browser:
            cmd.append("--no-browser")
        self.children.append(subprocess.Popen(cmd))
        return f"http://127.0.0.1:{port}/"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
```

(`ruff format` will rewrap the `cmd` list; let it.)

- [ ] **Step 3: The header's button and dialog.** In `header.py`'s `__init__`, after `self.backend_why`:

```python
            self.other = (
                ui.button("Open in another backend…", on_click=self._open_other)
                .props("flat dense no-caps")
                .mark("other-backend")
            )
```

in `refresh`, after the `backend_why` lines: `self.other.set_enabled(project is not None)`; and the method, after `_change_backend`:

```python
    def _open_other(self) -> None:
        """A second tool process on another backend (brief §3.6): its own
        port and browser tab; one project and backend per process."""
        project = self.shell.session.project
        if project is None:
            return
        options = sorted(set(project.config.backends) | {project.config.backend_name})
        self.dialog.clear()
        with self.dialog, ui.card():
            ui.label("Open this project in another backend").classes("text-h6")
            ui.label(
                "A second dstf author process on its own port, opening its own browser "
                "tab; one project and backend per process. Stop server here ends it too."
            ).classes("text-xs text-secondary")
            choice = (
                ui.select(options, value=project.config.backend_name, label="backend")
                .props("dense outlined")
                .mark("other-choice")
            )
            link = ui.link("", "/", new_tab=True).mark("other-link")
            link.set_visibility(False)

            def start() -> None:
                url = self.shell.spawn(str(choice.value))
                link.set_text(f"open {url}")
                link.props(f'href="{url}"')
                link.set_visibility(True)

            with ui.row():
                ui.button("Start", on_click=start).props(
                    "unelevated dense no-caps color=primary"
                ).mark("start-other")
                ui.button("Close", on_click=self.dialog.close).props("flat dense no-caps")
        self.dialog.open()
```

The module docstring's last sentence ("Open in another backend…" joins in Task 8) goes.

- [ ] **Step 4: Run green.** `uv run --no-sync pytest -q tests/test_shell.py`. Expected: all pass. If `test_changing_the_backend_reopens…` hangs in `_settled` on `backend.value`, the select's `on_change` fired for the programmatic `set_options(value=…)` with the OLD value before the reopen committed — check `Header.refresh` skips `set_options` while `load.in_progress`.

- [ ] **Step 5: Gates, then commit** `src/dstf/authoring/shell/shell.py src/dstf/authoring/shell/header.py tests/test_shell.py` with message `shell: "Open in another backend…" starts a second process on a free port; the backend select's reopen and refusal guarded`.

**Named mutations:**
- M8.1: `spawn` omits `--backend` → the "second process" test fails at the `author` slice.
- M8.2: `_backend_why` ignores pending edits → the greyed test fails at `enabled`.
- M8.3: `spawn` always appends `--work-dir` (`str(self.work_dir)` → `"None"`) → the no-browser test fails at `"--work-dir" not in cmd`.
- M8.4: `_absorb` on `opened` with an error sets `phase = "failed"` when a project is open (the same as M5.1, re-applied here for the reopen) → `test_a_backend_that_cannot_be_built…` fails at `shell.load.phase == "done"`.

**Done when:** the select reopens on "Python (alt)" and snaps back after a refused reopen; the spawned command line is as the test reads it; the suite is green.

---

### Task 9: The real page in Chromium

The browser test for this sub-project's shell (the old `tests/test_author_smoke_playwright.py` went with the old shell in Task 3): the process serves, the header draws, rows fill in with verdicts, the chips carry the classifier's counts, the body's font stack ends in a generic sans-serif, nothing scrolls sideways, a check survives a click into the Test page and back, the batch buttons carry their counts, Stop server ends the process, and the spec file is untouched.

**Files:**
- Create: `tests/test_author_suite_playwright.py`

- [ ] **Step 1: The test.**

```python
"""The Suite page and the Test page stub in Chromium, served by `dstf author`
(brief §3.2, §3.3, §3.8; roadmap §2's gate predicates over FakeLib). NiceGUI's
user simulation renders nothing, so this says the page a user opens works
end to end: the process serves on 127.0.0.1 within seconds, the rows fill in
with verdicts, a check survives a click into a test and back within the tab,
the buttons say their counts, the font token reaches the body, nothing
scrolls sideways, and Stop server ends the process.

Selectors: NiceGUI's `.mark()` is server-side only, so elements are located
by the `dstf-*` classes and by role and name."""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("nicegui")
pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.playwright

from playwright.sync_api import expect  # noqa: E402

from test_session import A, D, _project  # noqa: E402

READY_TIMEOUT = 30.0  # seconds to wait for the server's first 200
EXPECT_TIMEOUT = 15_000  # ms; a refresh is a websocket round trip


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _start(cfg: Path, work: Path, log: Path) -> tuple[subprocess.Popen, str]:
    """`dstf author` as a plain process: NiceGUI's `ui.run` treats an
    inherited PYTEST_CURRENT_TEST as "I am a screen test" and demands
    NICEGUI_SCREEN_TEST_PORT, ignoring --port."""
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
    sink = log.open("wb")
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "dstf", "--config", str(cfg), "author",
            "--work-dir", str(work), "--port", str(port), "--no-browser",
        ],
        stdout=sink,
        stderr=subprocess.STDOUT,
        env=env,
    )
    return proc, f"http://127.0.0.1:{port}/"


def _wait_until_serving(url: str, proc: subprocess.Popen, log: Path) -> float:
    """Seconds to the first 200 — the page must appear before the results
    are read, so this is short even over a large project."""
    started = time.monotonic()
    deadline = started + READY_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"dstf author exited with {proc.returncode}\n{log.read_text()}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return time.monotonic() - started
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.2)
    pytest.fail(f"no 200 from {url} within {READY_TIMEOUT}s\n{log.read_text()}")


def _row(page, short_name: str):
    """The row whose name button reads `short_name` exactly."""
    return page.locator("div.dstf-tests .dstf-row").filter(
        has=page.get_by_role("button", name=short_name, exact=True)
    )


def test_the_suite_page_fills_in_keeps_its_checks_across_a_click_and_stops(
    tmp_path, playwright_browser
):
    cfg, work = _project(tmp_path)
    spec_file = cfg.parent / "test_spec.json"
    before = spec_file.read_bytes()
    log = tmp_path / "author.log"
    proc, url = _start(cfg, work, log)
    try:
        _wait_until_serving(url, proc, log)
        context = playwright_browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(url)

        # The header draws and the rows fill in with their verdicts.
        expect(page.locator(".dstf-header")).to_contain_text("DSTF", timeout=EXPECT_TIMEOUT)
        expect(page.locator(".dstf-header")).to_contain_text("FakeLib", timeout=EXPECT_TIMEOUT)
        rows = page.locator("div.dstf-tests .dstf-row")
        expect(rows).to_have_count(5, timeout=EXPECT_TIMEOUT)
        badges = page.locator("div.dstf-tests .dstf-badge")
        expect(badges.filter(has_text="PASS")).to_have_count(2, timeout=EXPECT_TIMEOUT)
        expect(badges.filter(has_text=re.compile(r"^FAIL$"))).to_have_count(1)
        expect(badges.filter(has_text="SIM_FAIL")).to_have_count(1)
        expect(badges.filter(has_text="NO_REF")).to_have_count(1)
        # The chips carry the classifier's counts.
        chips = page.locator(".dstf-chip")
        expect(chips.filter(has_text="PASS 2")).to_have_count(1, timeout=EXPECT_TIMEOUT)
        expect(chips.filter(has_text=re.compile(r"^FAIL 1$"))).to_have_count(1)  # not SIM_FAIL 1

        # The font token reaches the body (roadmap §1 predicate 2's last gap):
        # the stack ends in a generic sans-serif.
        font = page.evaluate("() => getComputedStyle(document.body).fontFamily")
        assert font.strip().endswith("sans-serif"), font
        # Nothing scrolls sideways (the old shell's tab strip did).
        layout = page.evaluate(
            "() => ({scroll: document.documentElement.scrollWidth,"
            " client: document.documentElement.clientWidth, x: window.scrollX})"
        )
        assert layout["scroll"] <= layout["client"] and layout["x"] == 0, layout

        # Check D; the batch buttons say their object and count.
        _row(page, D.rsplit(".", 1)[-1]).get_by_role("checkbox").click()
        accept = page.get_by_role("button", name=re.compile(r"^Accept 1 result as baselines"))
        expect(accept).to_be_enabled(timeout=EXPECT_TIMEOUT)
        rerun = page.get_by_role("button", name=re.compile(r"^Rerun 1 selected test \(Python\)"))
        expect(rerun).to_be_enabled()

        # A click on A's name views A: the Test page, in this tab.
        page.get_by_role("button", name=A.rsplit(".", 1)[-1], exact=True).click()
        expect(page).to_have_url(re.compile(r"/test/FakeLib\.A$"), timeout=EXPECT_TIMEOUT)
        expect(page.locator(".dstf-test")).to_contain_text("FakeLib.A", timeout=EXPECT_TIMEOUT)
        expect(page.locator(".dstf-test .dstf-badge")).to_have_text("FAIL", timeout=EXPECT_TIMEOUT)
        # Its TEST-placement buttons speak of this one test.
        expect(
            page.get_by_role("button", name=re.compile(r"^Rerun 1 selected test \(Python\)"))
        ).to_be_visible(timeout=EXPECT_TIMEOUT)

        # Back to the Suite: D is still checked, A is not (viewing ≠ selecting).
        page.get_by_role("link", name="← Suite").click()
        expect(page.locator("div.dstf-tests .dstf-row")).to_have_count(5, timeout=EXPECT_TIMEOUT)
        expect(_row(page, D.rsplit(".", 1)[-1]).get_by_role("checkbox")).to_have_attribute(
            "aria-checked", "true", timeout=EXPECT_TIMEOUT
        )
        expect(_row(page, A.rsplit(".", 1)[-1]).get_by_role("checkbox")).to_have_attribute(
            "aria-checked", "false"
        )
        assert errors == []

        # Stop server ends the process (no confirm: nothing pending, no job).
        page.get_by_role("button", name="Stop server").click()
        proc.wait(15)
        assert proc.returncode is not None
        context.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)

    assert spec_file.read_bytes() == before  # nothing was saved
```

- [ ] **Step 2: Run.** `uv run --no-sync pytest -q -m playwright tests/test_author_suite_playwright.py > <scratchpad>/pw.log 2>&1; echo "exit $?"; grep -E "passed|failed|error" <scratchpad>/pw.log | tail -1`. Expected: `1 passed`. A failure at `to_have_attribute("aria-checked", …)` means Quasar's checkbox renders its state elsewhere: read `page.locator(".q-checkbox").first.evaluate("el => el.outerHTML")` from the failure and fix the assertion to the attribute Quasar 2 uses (`aria-checked` on the `div[role=checkbox]` is documented; the assertion is what the red-state run could not reach without the shell).

- [ ] **Step 3: Gates, then commit** — `git add tests/test_author_suite_playwright.py` — with message `browser test: the Suite page fills in, keeps its checks across a click into the Test page, says its counts, applies the font token, never scrolls sideways, and Stop server ends the process`.

**Named mutations:** none new — the browser test is the gate of Tasks 5–8's real rendering; the review runs it once against the tree and reports its wall time (the `_wait_until_serving` return value, printed via `-s` or a `print` the reviewer adds locally).

**Done when:** the browser test passes; `uv run --no-sync pytest -q -m playwright` reports no skipped and no failed test, and its collection includes `tests/test_author_suite_playwright.py` and no `tests/test_author_smoke_playwright.py`.

---

### Task 10: Docs — D129, the roadmap block, the deleted behaviours named

**Files:**
- Modify: `docs/decisions.md` (D129), `docs/superpowers/plans/2026-09-15-authoring-tool-redesign-roadmap.md` (§2 status, §3 and §4 lines)

- [ ] **Step 1: D129.** Append to `docs/decisions.md`:

```markdown
## D129 — Selection is the page's; capabilities attach by placement (2026-09-16)

The authoring tool's selection — the checked tests, the variable, the
criterion's path — is the state of a page in one browser tab, kept in
NiceGUI's tab storage, and reaches a capability through the view the page
builds for it: `SessionView(session, selection)`. The Session holds none;
`Session.select`, `Session.selection` and the `selection` event are gone. Two
tabs on one server keep two selections, which the one-tab shell (D125's tool,
sub-projects 4–6) could not — its `Shell.select_one` wrote the Session's
selection, so a second tab's click moved the first tab's plot. The Test page
(redesign sub-project 3) needs the same for its variable and criterion, so
the move was made once, here, where the shell was being rewritten anyway.

With it, `Scope` — where a capability's TAB appeared — became `Placement`,
where its BUTTON attaches: `SUITE` (the Suite page's batch bar), `TEST` (the
Test page's header), `VARIABLE` (a variable section's toolbar), `CRITERION`
(the inline criterion editor); a capability names a frozenset of them and a
`caption(view)`, the sentence its button carries (object, effect, count). The
shell draws one outlined button per capability at each placement and hosts
its panel in a dialog; it still knows no capability by name. `PROJECT` has no
successor because Close project is gone and the project-less state is
unreachable. The contract's spec is amended in §4.6 of the two-tier design;
`tests/test_capability_conformance.py` is the executable form over the four
placements and the five outcomes.

Two behaviours moved with the structure. `Session.open` runs on a thread the
shell owns and every Session event crosses to the UI loop through one queue
an app-level timer drains, so the page appears before a result is read and
rows fill in per `loading` event (brief §3.8); a model whose scoring raised is
announced with `error=True`, and `open` emits `done` and starts the rescan
even then. And the old shell's tests of behaviours the Test page re-hosts
(panels following a rescore, the leaf's config error above the editor, an
error cleared in place, the plot redrawn in place after a drag) were deleted
and named in the roadmap's sub-project 3 and 4 blocks rather than carried as
text: their shape depends on the page sub-project 3 designs. `Session.close`
stays as the library's counterpart to `open` — the Session is also driven
without a page — although no page calls it since Close project went.
```

- [ ] **Step 2: The roadmap.** In `docs/superpowers/plans/2026-09-15-authoring-tool-redesign-roadmap.md`, §2 gains, after its **Gate.** paragraph:

```markdown
**Status (built 2026-09-DD).** Built as
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
```

(`DD` is the day the build lands; the build session writes it. The gate paragraph is Task 11's.) §3's **Goal.** paragraph gains a closing sentence: "Re-hosts what went with the old shell (sub-project 2): the plot redrawn in place after a drag through the figure hook, a refused drag shown in the editor's slot and snapped back (the two `tests/test_edit_on_plot.py` hook tests), and the figure hook's per-owner clearing on a rebuilt panel." §4's **Goal.** paragraph gains: "Re-hosts the old shell's editor behaviours deleted with it (sub-project 2): a panel follows an edit another capability, a Discard, a Save or a Reload staged; the criterion's config error shows above its editor; a capability's error shows in its editor and a later success clears it in place, keeping the field being typed in; the editor a selection lands on is the first enabled one; the tree editor's flow through its controls — a wrap the validator refuses shown in place, add, k-of-n, remove — with the tree rows following each edit (the old `test_edit_tree_wrap_add_remove_and_combinator_edits`)."

- [ ] **Step 3: Commit** `docs/decisions.md docs/superpowers/plans/2026-09-15-authoring-tool-redesign-roadmap.md` with message `docs: D129 (selection is the page's; capabilities attach by placement); roadmap sub-project 2 status, the re-hosted behaviours named in §3 and §4`.

**Done when:** D129 is the last entry of `docs/decisions.md`; the roadmap's §2 has a Status block and §3/§4 name the deleted behaviours; `git diff --stat HEAD~1` touches only those two files.

---

### Task 11: The real-library gate over a COPY of the TRANSFORM work dir (the orchestrator's)

Roadmap §2's gate: 331 rows appear progressively; the header's counts match `dstf compare`'s; nothing written; the page never scrolls sideways; a screenshot READ. Plus this plan's own claims over real data: first 200 within seconds of process start (brief §3.8 "the page appears within about a second" — measured from the process start, NiceGUI's own startup included), the chips over a `--filter`ed set (mutation M6.6's observable), the batch captions with three checked, the font token on the body, and `Session.open`'s partial-failure path is NOT exercised here (no model of TRANSFORM fails scoring).

**Setup** (memory: plan-tests-from-scratchpad, transform-verification-setup): `cp -r testing_output/TRANSFORM/Dymola/linux <scratchpad>/gate/work` (3.6 GB, ~2 min); `CFG=/mnt/d/Modelica/TRANSFORM-UnitTests/ReferenceResults/testing.json`; before AND after: `md5sum` of that config's `test_spec.json`, `ls -la` of the reference dir (`.../ReferenceResults/Dymola/linux`), `ls` of the REAL work dir and of `$XDG_CACHE_HOME/dstf/scan/` (the real cache: the gate runs with `XDG_CACHE_HOME` pointed at a scratchpad dir so the tool's cache write lands there). One driver per command (the memory watchdog).

- [ ] **Step 1: The driver.** `<scratchpad>/gate/suite_gate.py`:

```python
"""Roadmap §2's gate over a COPY of the TRANSFORM work dir: time to first 200,
the progress line seen non-empty then empty, 331 rows, the chips against
`dstf compare`'s verdict counts, the batch captions with three checked, the
font token, no sideways scroll, a screenshot; nothing written."""
import json, os, re, subprocess, sys, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

REPO = Path("/mnt/d/Modelica/DynamicSystemsTestingFramework")
CFG = "/mnt/d/Modelica/TRANSFORM-UnitTests/ReferenceResults/testing.json"
WORK = sys.argv[1]          # the COPY
out = Path(sys.argv[2]); xdg = sys.argv[3]
PORT = 8765
env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
env["XDG_CACHE_HOME"] = xdg
log = open(out / "author.log", "w")
t0 = time.monotonic()
proc = subprocess.Popen(
    [str(REPO / ".venv/bin/python"), "-m", "dstf", "--config", CFG, "author",
     "--work-dir", WORK, "--port", str(PORT), "--no-browser"],
    cwd=str(REPO), env=env, stdout=log, stderr=subprocess.STDOUT)
res = {}
try:
    while True:
        if proc.poll() is not None:
            raise SystemExit(f"server exited {proc.returncode}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=5) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(0.2)
        if time.monotonic() - t0 > 120:
            raise SystemExit("timeout waiting for 200")
    res["first_200_s"] = round(time.monotonic() - t0, 1)
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1600, "height": 1000})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(f"http://127.0.0.1:{PORT}/")
        pg.locator(".dstf-header").wait_for(timeout=30000)
        res["header_s"] = round(time.monotonic() - t0, 1)
        progress = pg.locator(".dstf-progress")
        seen = []
        rows = pg.locator("div.dstf-tests .dstf-row")
        counts_over_time = []
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            text = progress.inner_text() if progress.count() and progress.first.is_visible() else ""
            n = rows.count()
            scored = pg.locator("div.dstf-tests .dstf-badge").filter(has_text=re.compile(r"^(PASS|FAIL|SIM_FAIL|NO_REF)$")).count()
            counts_over_time.append((round(time.monotonic() - t0, 1), text, n, scored))
            if n == 331 and scored == 331 and text == "":
                break
            time.sleep(1.0)
        res["fill"] = counts_over_time[:5] + counts_over_time[-3:]
        res["rows"] = rows.count()
        res["progressive"] = any(0 < s < 331 for _, _, _, s in counts_over_time)
        res["chips"] = {k: pg.locator(".dstf-chip").filter(has_text=re.compile(rf"^{k} ")).inner_text() for k in ("PASS", "FAIL", "SIM_FAIL", "NO_REF")}
        res["body_font"] = pg.evaluate("getComputedStyle(document.body).fontFamily")
        res["layout"] = pg.evaluate("() => ({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth, x: window.scrollX})")
        for i in range(3):
            rows.nth(i).get_by_role("checkbox").click()
        pg.wait_for_timeout(500)
        res["captions"] = pg.locator(".dstf-actions button").all_inner_texts()
        pg.screenshot(path=str(out / "suite.png"))
        pg.locator("div.dstf-tests .dstf-row").nth(0).get_by_role("button").first.click()
        pg.wait_for_url(re.compile(r"/test/"), timeout=15000)
        pg.locator(".dstf-test .dstf-badge").wait_for(timeout=15000)
        res["test_page"] = pg.locator(".dstf-test").inner_text()[:200]
        pg.screenshot(path=str(out / "test.png"))
        res["errors"] = errors
        b.close()
finally:
    proc.terminate(); proc.wait(10)
(out / "gate.json").write_text(json.dumps(res, indent=2))
print(json.dumps(res, indent=2))
```

Run: `mkdir -p <scratchpad>/gate/out <scratchpad>/gate/xdg && PLAYWRIGHT_BROWSERS_PATH=$HOME/.cache/ms-playwright uv run --no-sync python <scratchpad>/gate/suite_gate.py <scratchpad>/gate/work <scratchpad>/gate/out <scratchpad>/gate/xdg > <scratchpad>/gate/out/driver.log 2>&1; echo "exit $?"`. READ `gate.json`. Then run it a second time (the warm open: the scan cache under the scratchpad XDG is now written) and READ the second `gate.json`.

- [ ] **Step 2: `dstf compare` over the COPY**, in a separate command: `uv run --no-sync dstf --config $CFG compare --work-dir <scratchpad>/gate/work > <scratchpad>/gate/out/compare.log 2>&1; echo "exit $?"`; the summary counts are read from the log's tail (exit 1 is the recorded SIM_FAIL). The chips in `gate.json` must equal them: expected `PASS 330`, `FAIL 0`, `SIM_FAIL 1`, `NO_REF 0` (memory: 330 PASS + 1 SIM_FAIL, SteamWater_HCSG).

- [ ] **Step 3: The `--filter` chips** (mutation M6.6's observable): `python -m dstf --config $CFG author --work-dir <copy> --filter "TRANSFORM.Fluid.*" --port 8766 --no-browser` driven by a 20-line variant of the driver that reads only `rows.count()` and the chips after the fill; the chips must sum to the row count, not to 331.

- [ ] **Step 4: The screenshot READ** — as a thumbnail (`render_check.py thumb <out>/suite.png -o <scratchpad>/suite.thumb.jpg`, then Read the thumbnail; the same for `test.png`). What to see: the off-white header with "DSTF", the library name, the backend select, "idle", "0 pending edits", Save/Discard/Export greyed with "no pending edits" beside Save, "Stop server"; the toolbar with filter, grouped/short toggles and the four chips; two outlined batch buttons with counts; grouped rows with short names and filled badges; the runs card reading "no job running". On the Test page: the header, "← Suite", the model id, its badge, two buttons, the sub-project 3 note.

- [ ] **Step 5: Predicates and the roadmap paragraph.** Every one must hold: `first_200_s` under 5 s both cold and warm (NiceGUI's own start is most of it; the open no longer gates the first response — compare the previous gate's 18 s warm / 46 s cold); `progressive` True on the cold run (some sample with 0 < scored < 331); `rows == 331`; the chips equal `compare`'s counts; `body_font` ends in `sans-serif`; `layout.scroll <= layout.client`; `captions` read `Accept N results as baselines` and `Rerun 3 selected tests (Dymola)` with N ≤ 3; `errors == []`; the before/after listings identical (spec md5, reference dir, real work dir, real cache dir); the scratchpad XDG holds one scan cache file after the first run. Then append the **Gate over TRANSFORM (2026-09-DD, over a copy…)** paragraph to the roadmap's §2 Status block with the numbers, and commit it by path with message `roadmap: sub-project 2 gated over TRANSFORM`.

**Done when:** every predicate of Step 5 holds and the roadmap carries the numbers; the copy and the scratchpad are the only things that changed on disk.

---

## Self-review (run by the writer before the plan's review)

**Spec coverage** — roadmap §2 and the brief, requirement → task: two pages, click views (6); viewing ≠ selecting, checkboxes never blank the view (6); every flat/grouped × short/full combination, grouped-short default (6); filters (6: text + chips); batch actions say object/effect/count (4, 7); greyed controls say why (5, 7: `Action`); progressive startup — page within a second, rows fill in, a test opens as soon as scored (5, 6; the Test page reads its badge from `comparisons` at build time and on events); batch actions unlock when loading finishes (7); dashboard link works at once (5: the route is mounted before the open); library scan cached — sub-project 1's, consumed unchanged; per-browser-tab selection (6: `tabstate.py`); placement model replacing the tab strip, spec §4.6 amended (4, 7); Accept and Rerun at suite AND test (4, 7); header per DESIGN.md — DSTF, library, backend, queue indicator's slot, pending count with Save/Discard/Export, Stop server with confirmations, dashboard link (5); Close project removed (5: no such button); change backend reopens, refused while edits pending or a run is in flight (8); Open in another backend… starts a second process (8); old `app.py` deleted (3); Test page stub (6); the `loading(results)` error flag and `done` after a partial failure (1). Not in this plan, by the roadmap: the queue and its indicator's content (§5), "with 1 unsaved simulation edit" beyond the count (the caption counts edits; §5 refines), themes (§7).

**Placeholder scan** — no "TBD", no "similar to Task N": the rewrite rules of Task 3 name every affected test and its disposition; the runs-card port of Task 5 names the ten tests and the four substitutions; `DD` in Task 10/11 is the build date, written by the build session.

**Type consistency** — `Shell(config_path, *, work_dir, filter, package, show_browser)` in Tasks 5–9; `SessionView(session, selection)` in 3–7; `Placement` values in 4, 7, 10; `Action(on_click, *, props, marker, why)` in 5–7; `plural(count, noun)` in 4–8; `ActionBar(shell, placement, selection_of, *, marker)` in 7; `LoadState.phase` strings `idle | scan | results | done | failed` in 5–8; the markers `wordmark project backend backend:why other-backend other-choice other-link start-other runs pending save save:why discard export stop stop-anyway stop-reason drawer-model drawer-op progress needle grouping naming counts chip:<S> check-shown uncheck uncheck:why tests group:<pkg> check:<model> <model> suite-actions test-actions action:<name> action:<name>:why error:<name> panel:<name> close:<name> back model status runs-card job-row` are each defined in one task and read in the tests that name them.
