# The Foundations Corrections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the production corrections sub-project 1 (`2026-09-15-the-foundations.md`) held back — the tool's page carrying `axes.js` with a wired check that survives NiceGUI's own listener, the Session's base-trace cache following the scored list, the rescan re-reading a changed model's result, a scan-cache write that can fail without failing the open, every scored test announced — and, first, a pre-existing data-integrity bug the last review found beside them: `run --merge` / `--rerun` read results back with no run verdict, so a timed-out run's partial `.mat` reads as PASS and can be offered for acceptance as the primary baseline.

**Architecture:** Every correction is made where the state it corrects lives, never at a call site: the run verdict moves INTO `Backend.read_results` (the one reader), so no caller can forget it; `axes.js` asks the emitter for ITS OWN handler and follows `plotly_react`; the Session keys its cached bases on the scored `(name, index)` list, re-reads a changed model with its new `TestModel` before rescoring, announces post-loop scored tests and survives one model's scoring failure with complete results; `store_scan` treats an unwritable cache dir as a warning and names its temporary per thread. Nothing here changes a layout, a format on disk or a CLI flag.

**Tech Stack:** Python 3.10+, numpy, NiceGUI 3.16 (`[author]`), the vendored plotly.js 2.35.0 (report) and NiceGUI's bundled plotly.js 3.1.1 (tool), pytest, Playwright + Chromium, uv.

**Provenance and re-derivation.** Each held item arrived with a reproducer and, for most, a remedy a reviewer tried. A reviewer's remedy is a correction and the least-verified text in a diff, so each was re-derived from the code before being written here; where this plan differs from the tried remedy it says so in the task. The build session does not consult the tried remedies — the task text is the specification. The record of the holds (the build log, the probe files, the held Task 3 patch) is outside the tracked tree; Task 3 names the patch's path because the build session applies it, nothing else is needed from there.

**Owner calls this plan takes, marked for the owner:**
1. **The run-verdict bug goes first (Task 1).** It is pre-existing, outside sub-project 1, and the only item that can corrupt the reference tree another repo version-controls. Leading with it is the writer's recommendation, not an instruction; the owner may move it to a plan of its own or defer it — every other task is independent of it.
2. **Out of scope, on purpose:** the tool's body font (Quasar's stack; the token is applied by sub-project 2's shell), the "first click under 1 s" target (the click's time is the current shell's; Session.figure is 72 ms), the `--dstf-warn-dark` contrast question (a report-wide restyle the owner has not ruled on), and the partial-rerun verdict flip over OLD data in `docs/ideas.md` (2026-09-14 tail): that one is `status.json`'s subset snapshot losing rows for manifest entries with no `last_run_status`, a different cause from Task 1's, and stays with sub-project 5.

---

## Conventions for every task

- Run from the repo root with `uv run --no-sync ...` (agents share the venv). Gates before every commit, each redirected to a log whose summary line is READ, never piped through `tail` (a pipe returns the tail's exit code, and a run with one ERROR printed green last time): `uv run --no-sync ruff check`, `uv run --no-sync ruff format --check` (fix with `ruff format <files>`), `uv run --no-sync mypy` **bare, no path**, `uv run --no-sync pytest -q -m "not playwright" > <scratchpad>/gate.log 2>&1; echo "exit $?"; grep -E "passed|failed|error" <scratchpad>/gate.log | tail -1`, and `uv run --no-sync pytest -q -m playwright` the same way — the browser tests RUN, they do not skip.
- **Red before green, shown.** Every test step runs the new test on the unchanged code first and the step says what fails and where. A test that does not fail on the unchanged code — or on the named mutation, for tests that guard against a regression rather than reproduce a defect — is not landed until it does.
- **Named mutations.** Each task lists the mutations its tests must catch. The task's fresh-context review applies each one in a `git archive` copy (absolute `PYTHONPATH`, the reviewer prints the module's `__file__` first — a relative path imported the real tree once and every mutant passed) and reports which survived. A surviving mutation is a plan defect: fix the test, not the mutation list.
- **Commit with explicit paths — `git commit -F <msgfile> -- <paths>` — never a bare `git commit`, never `--amend`.** New files need `git add <file>` first. Messages end with the attribution trailer the session's instructions specify. Nothing is pushed.
- **Never run `dstf run` / `dstf compare` / `dstf author` against `testing_output/` or any real work directory** from a task or a review. Task 9's real-library gate is the orchestrator's and works over a COPY. Tests use `tmp_path`; the autouse `_scan_cache_home` fixture in `tests/conftest.py` already redirects `XDG_CACHE_HOME` (a test that writes the user's real cache is a defect). A shell that exports `XDG_CACHE_HOME` for a script also moves Playwright's browser lookup — set `PLAYWRIGHT_BROWSERS_PATH=$HOME/.cache/ms-playwright` in that shell.
- **Done-checks are predicates over the end state**, never counts and never "this grep returns nothing".
- Implementers run sequentially in the main tree (the editable `.pth` imports the main tree, so a worktree does not isolate them); reviewers run on archives, at most two in parallel, and never alongside a full suite (memory).
- `docs/ideas.md` is not touched. `docs/decisions.md` gains exactly one entry, D128, in Task 8.
- **Every file under `src/dstf/authoring/` other than `app.py`, `forms.py` and `builtin/*` still imports no UI framework** (sub-project 1's predicate 5).

## File structure

| Path | Responsibility after this plan |
|---|---|
| `src/dstf/backends/base.py` | `read_results` applies the run verdict (`run_verdicts` + `_force_failed`) to every result before storing it and before `on_result`; `read_last_results` is `read_results` over the manifest on disk. |
| `src/dstf/pipeline.py` | Unchanged code; `RunOutcome`'s docstring says results carry the run verdict. |
| `src/dstf/reporting/templates/axes.js` | `isWired` asks the emitter for the node's OWN handler (`gd._dstfFollow`); the follow is registered on `plotly_restyle` AND `plotly_react`. |
| `src/dstf/authoring/app.py` | `Shell.root()` adds `axes.js` to the page body (the held Task 3). |
| `src/dstf/authoring/session.py` | `_ModelReads.scored` + `_scored_key`; `figure` raises `KeyError` before touching the cache and drops the bases when the key moves; `_rebuild_tests(models, reread=...)` + `_reread_results`; `_reload_results` announces post-loop scored tests, dedupes announcements, survives one model's scoring failure and raises once. |
| `src/dstf/discovery/scan_cache.py` | `store_scan` returns `None` with a warning on `OSError`; the temporary is named per process AND thread. |
| `tests/test_pipeline.py` | The verdict-at-the-read test; the merge test asserts the merged-in verdict. |
| `tests/test_cli.py` | The merged outcome reaches the interactive review with its verdict: a timed-out test is never offered for acceptance. |
| `tests/test_compare_accept.py` | The docstring that recorded the live bug now records the fix. |
| `tests/test_axes_js_playwright.py` | Foreign-listener and `Plotly.react` tests; the page keeps a pristine copy of the figure. |
| `tests/test_report_suite.py` | The part-order test that could not fail is deleted. |
| `tests/test_interactive_report.py` | The one-axis docstring corrected. |
| `tests/test_author_smoke_playwright.py` | The tool: a legend click on an error trace shows its axis (from the held patch). |
| `tests/test_session.py` | Stale-base probes, `figure` KeyError, dropped reads; rescan re-read + the three probes c/d/e; announced post-loop tests; scoring-failure completeness; an open that cannot cache. |
| `tests/test_scan_cache.py` | Unwritable cache dir → None + warning; two threads storing one key. |
| `docs/decisions.md` | D128. |
| `docs/superpowers/plans/2026-09-15-authoring-tool-redesign-roadmap.md` | Sub-project 1's status block: corrected, gated. |

---

### Task 0: The venv and the baseline gate

**Files:** none changed.

- [ ] **Step 1: Confirm the venv carries every extra and Playwright.** `uv run --no-sync python -c "import nicegui, playwright.sync_api, fmpy; print(nicegui.__version__)"` prints `3.16.0`. If any import fails: `uv sync --extra dev --extra fmpy --extra om --extra author --locked --inexact` then `uv run --no-sync playwright install chromium`.

- [ ] **Step 2: The baseline gate, read from the log.** Run the four gates of the Conventions and record the summary lines. Expected at the plan's HEAD (91abfc2): ruff and format clean outside `docs/design/authoring-tool/`, bare mypy clean, `2071 passed, 3 skipped` (not playwright), `27 passed` (playwright, no skips). A different baseline is not a stop — record it in the handoff and go on; a failing baseline IS a stop.

- [ ] **Step 3: Confirm the held patch's TEST hunk applies without applying it.** `git apply --check --include='tests/*' .soul/foundations-task3-held.patch` prints nothing (it did on 2026-09-16). The `app.py` hunk no longer applies — the import block moved on since it was saved — and Task 3 writes it by hand; if the test hunk has stopped applying too, Task 3 Step 1 has the test's full text.

**Done when:** the three commands above have produced their expected output in this session's log.

---

### Task 1: The run verdict is applied at the read — in `read_results`, the one reader

**Why here and not at the call site.** `run_verdicts` (base.py) is the guard that stops a partial or stale `.mat` from reading back as a success; today only `read_last_results` applies it. `run_pipeline` reads back through `read_results` directly, and with `merge` (which `--rerun` forces) the merged-in models have no in-memory run result, so their on-disk artifacts are taken at face value: `run --merge` reports PASS where `compare` on the same work dir reports SIM_FAIL, and `run --rerun failed -i` hands the interactive review those unverdicted results, where `a` stores them as the primary baseline. Putting the verdict inside `read_results` means every reader carries it and there is no second place to forget.

**Files:**
- Modify: `src/dstf/backends/base.py` (`read_results` ~579-670, `read_last_results` ~672-704)
- Modify: `src/dstf/pipeline.py` (`RunOutcome` docstring only)
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_compare_accept.py:150-161` (docstring)

- [ ] **Step 1: The reader test.** In `tests/test_pipeline.py` add, after the imports, `import json` and `from dstf.storage.batch_manifest import BatchManifest`, then this helper and test:

```python
def _stamp(work, model_id: str, status: str) -> None:
    """Record ``status`` as ``model_id``'s last run in the work dir's batch
    manifest — what a timed-out run leaves behind beside a partial result
    file that still parses as a success."""
    path = work / "batch_manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data.values():
        if entry["model_id"] == model_id:
            entry["last_run_status"] = status
    path.write_text(json.dumps(data), encoding="utf-8")


def test_read_results_applies_the_run_verdict_before_storing_and_before_the_callback(
    example,
):
    """The manifest's verdict is authoritative over the result file: a test
    the run recorded as timed out is forced to success=False by the reader
    itself, so no caller — run_pipeline's merged read included — sees the
    file's green. Both the returned dict and the per-result callback carry
    the verdict."""
    config, all_tests, backend, store = example
    constant = _pick(all_tests, CONSTANT_CSV)
    run_pipeline(config, constant, backend=backend, store=store)
    _stamp(config.work_dir, CONSTANT_CSV, "timed_out")

    manifests = [BatchManifest.load(config.work_dir / "batch_manifest.json")]
    seen = []
    results = backend.read_results(
        manifests, constant, on_result=lambda m, r: seen.append((m, r.success))
    )

    assert results[CONSTANT_CSV].success is False
    assert "timed_out" in (results[CONSTANT_CSV].error_message or "")
    assert seen == [(CONSTANT_CSV, False)]
```

- [ ] **Step 2: Run it to see it fail.** `uv run --no-sync pytest -q tests/test_pipeline.py -k applies_the_run_verdict` → FAILS at `assert results[CONSTANT_CSV].success is False` (the file reads as a success; nothing applied the verdict).

- [ ] **Step 3: The merged-read test.** In `test_merge_widens_the_read_scope_over_candidates`, between the `ramp_mtime = ...` line and `third = run_pipeline(...)`, insert:

```python
    # The ramp's run is now recorded as timed out — its result file is
    # untouched and still parses. The merged read must carry that verdict:
    # this is `run --rerun failed` reading the tests it did not re-run.
    _stamp(config.work_dir, SIMPLE_RAMP, "timed_out")
```

and replace `assert third.results[SIMPLE_RAMP].success` with:

```python
    assert third.results[SIMPLE_RAMP].success is False
    assert "timed_out" in (third.results[SIMPLE_RAMP].error_message or "")
```

The `ramp_mtime` assertion after it stays: the test was read, not re-run, and its manifest entry kept the verdict (`record_result` touches only the models it ran).

- [ ] **Step 4: Run it to see it fail.** `uv run --no-sync pytest -q tests/test_pipeline.py -k merge_widens` → FAILS at `assert third.results[SIMPLE_RAMP].success is False`.

- [ ] **Step 5: The review test.** In `tests/test_cli.py`, after `TestInteractiveReviewExitCode`, add:

```python
class TestMergedOutcomeReachesTheReviewWithItsVerdict:
    def test_a_timed_out_merged_in_test_is_never_offered_for_acceptance(
        self, tmp_path, monkeypatch, capsys
    ):
        """`run --rerun failed -i` then `a`: the review is handed the merged
        outcome. A merged-in test whose run timed out must arrive as a
        failed simulation — the loop skips it before any prompt — so its
        partial result can never become the primary baseline. Before the
        verdict moved into `read_results`, it arrived green (NO BASELINE),
        was prompted, and `a` stored it."""
        from conftest import python_example_copy
        from dstf.backends import dataset_kind_for, get_backend
        from dstf.comparison.comparator import compare_all
        from dstf.pipeline import run_pipeline

        ramp_id = "PythonTestingLib.Examples.SimpleRamp"
        constant_id = "PythonTestingLib.Examples.ConstantCsv"
        config, all_tests = python_example_copy(tmp_path, baselines=False)
        backend, store = get_backend(config), ReferenceStore(config)
        backend.quiet = True
        ramp = [t for t in all_tests if t.model_id == ramp_id]
        constant = [t for t in all_tests if t.model_id == constant_id]

        run_pipeline(config, ramp, backend=backend, store=store)
        path = config.work_dir / "batch_manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data.values():
            if entry["model_id"] == ramp_id:
                entry["last_run_status"] = "timed_out"
        path.write_text(json.dumps(data), encoding="utf-8")

        outcome = run_pipeline(
            config, constant, backend=backend, store=store, merge=True, candidates=all_tests
        )
        comparisons = compare_all(
            outcome.scope_tests,
            outcome.results,
            store,
            config.tolerance,
            config.default_points,
            dataset_kind=dataset_kind_for(backend),
        )
        # Accept everything the loop offers, in whatever order discovery
        # lists the two. A failed simulation is skipped without a prompt, so
        # only ConstantCsv is prompted and one `a` is consumed; the second
        # `a` is consumed only if the ramp IS prompted — the defect — and
        # then stores it.
        _feed_inputs(monkeypatch, ["a", "a"])
        rc = _interactive_review(
            outcome.scope_tests, outcome.results, comparisons, store, config
        )
        out = capsys.readouterr().out

        assert "Skipping (simulation failed)" in out
        assert store.get_reference(ramp_id) is None
        assert store.get_reference(constant_id) is not None
        assert rc == 1  # the timed-out test is still unresolved
```

`json`, `ReferenceStore`, `_interactive_review` and `_feed_inputs` are all in scope in `tests/test_cli.py` already (lines 4, 33, 27 and 662 on 2026-09-16).

- [ ] **Step 6: Run it to see it fail.** `uv run --no-sync pytest -q tests/test_cli.py -k never_offered` → FAILS at `assert "Skipping (simulation failed)" in out` (the ramp was prompted and accepted; `store.get_reference(ramp_id)` is not None).

- [ ] **Step 7: Implement.** In `src/dstf/backends/base.py`, `read_results` becomes:

```python
    def read_results(
        self,
        manifests: list[BatchManifest],
        tests: list[TestModel],
        on_result: Callable[[str, TestResult], None] | None = None,
    ) -> dict[str, TestResult]:
        """Read all simulation results from completed batch runs (parallel).
        ``on_result(model_id, result)`` is called on the calling thread as
        each result is stored.

        Every result carries the run's verdict. A test the manifests record
        as failed or timed out (``last_run_status``; :func:`run_verdicts`)
        is forced to ``success=False`` before it is stored and before the
        callback sees it, whatever its result file reads back as — a
        partial ``.mat`` from a timed-out run parses as a success. The
        verdict is applied in this one reader and not by its callers
        because the caller that forgot was ``run_pipeline``: ``run --merge``
        / ``--rerun`` reported PASS for a merged-in test whose run timed
        out while ``compare`` on the same work dir reported SIM_FAIL, and
        ``run --rerun failed -i`` then offered that partial run for
        acceptance as the primary baseline.
        """
        work_dir = self.config.work_dir
        assert work_dir is not None  # resolved in Config.__post_init__
        verdicts = run_verdicts(work_dir, manifests)
        test_lookup = {t.model_id: t for t in tests}
        results: dict[str, TestResult] = {}

        def _store(model_id: str, result: TestResult) -> None:
            if model_id in verdicts:
                _force_failed(result, verdicts[model_id])
            results[model_id] = result
            if on_result is not None:
                on_result(model_id, result)

        run_results = {}
        for manifest in manifests:
            for rr in manifest.results:
                run_results[rr.model_id] = rr

        # Build work items: (test_key, model_id, test_model, run_result)
        work_items = []
        for manifest in manifests:
            for test_key, entry in manifest.manifest.items():
                model_id = entry["model_id"]
                rr = run_results.get(model_id)
                test_model = test_lookup.get(model_id)

                if rr and not rr.success:
                    _store(
                        model_id,
                        TestResult(
                            model_id=model_id,
                            success=False,
                            error_message=rr.error_message or "Simulation failed",
                            statistics=rr.statistics if rr else None,
                        ),
                    )
                    continue

                if test_model is None:
                    _store(
                        model_id,
                        TestResult(
                            model_id=model_id,
                            success=False,
                            error_message="Test model not found in discovery",
                        ),
                    )
                    continue

                work_items.append((test_key, model_id, test_model, rr))
```

The rest of the method is unchanged except the store inside the `as_completed` loop, which becomes:

```python
                test_key, model_id, result, dt = future.result()
                _store(model_id, result)
```

(delete the two lines `results[model_id] = result` / `if on_result is not None: on_result(model_id, results[model_id])` there). `read_last_results` becomes:

```python
    def read_last_results(
        self,
        tests: list[TestModel],
        on_result: Callable[[str, TestResult], None] | None = None,
    ) -> dict[str, TestResult]:
        """Read results from the most recent batch run in the work
        directory: :meth:`read_results` over the manifest on disk, so each
        result reaches ``on_result`` with its run verdict already applied."""
        work_dir = self.config.work_dir
        if not work_dir.exists():
            return {}

        manifest_paths = sorted(work_dir.glob("batch_manifest.json"))
        if not manifest_paths:
            return {}

        manifests = [BatchManifest.load(p) for p in manifest_paths]
        return self.read_results(manifests, tests, on_result=on_result)
```

In `src/dstf/pipeline.py`, `RunOutcome`'s docstring gains one sentence after "whatever else the work dir's manifest holds from earlier runs.": `Every result carries its run verdict (``Backend.read_results``): a merged-in test whose last run failed or timed out is ``success=False`` here exactly as ``compare`` reports it.`

- [ ] **Step 8: Check no backend overrides `read_results`.** `grep -rn "def read_results\|def read_last_results" src/dstf/` lists only `src/dstf/backends/base.py`. If a backend overrides either, the override must call the base or apply `run_verdicts` itself — stop and report; do not proceed on an assumption.

- [ ] **Step 9: Run the three tests and the neighbours.** `uv run --no-sync pytest -q tests/test_pipeline.py tests/test_cli.py tests/test_compare_accept.py tests/test_session.py tests/test_backend_quiet.py tests/test_run_prologue.py tests/test_accept.py` → all pass. `test_session.py::TestOpen::test_read_last_results_reports_each_result_with_its_run_verdict_applied` still passes: it now exercises `read_results`' verdict through the wrapper.

- [ ] **Step 10: Correct the docstring that recorded the bug.** In `tests/test_compare_accept.py`, `test_manifest_verdict_forces_sim_fail_on_compare`'s docstring: replace the paragraph beginning `Says "a reader", not "compare":` with:

```
        Says "a reader", not "compare": this drives the two primitives, not a
        CLI. Every reader reaches them through `Backend.read_results`, which
        applies the verdict itself — `compare` via `read_last_results`, `run
        --merge` / `--rerun` via `run_pipeline` (tests/test_pipeline.py).
```

- [ ] **Step 11: Gate and commit.** The four gates (Conventions). `git commit -F msg -- src/dstf/backends/base.py src/dstf/pipeline.py tests/test_pipeline.py tests/test_cli.py tests/test_compare_accept.py`. Message: `backends: the run verdict is applied in read_results, the one reader — run --merge no longer reports a timed-out run's partial result as PASS`.

**Mutations the review applies (each must fail at least one test):**
- M1: delete the `_force_failed` call in `_store` → Step 1's test, Step 3's, Step 5's.
- M2: apply the verdict in `_store` but call `on_result` BEFORE `_force_failed` → Step 1's `seen == [(CONSTANT_CSV, False)]`.
- M3: keep the old `read_last_results` body (its own `_with_verdict` wrapper) and revert `read_results` → Step 1's and Step 3's (the direct `read_results` call and the pipeline's).
- M4: compute `verdicts` from `manifests[0].work_dir` instead of `self.config.work_dir` — must SURVIVE (they are the same dir); the reviewer confirms it survives and says so, so nobody "fixes" it later.

**Done when:** `backend.read_results(manifests, tests)` and `backend.read_last_results(tests)` over a work dir whose manifest records a model as `timed_out` both return that model with `success=False` and `"timed_out"` in its message, and `_interactive_review` over a merged `run_pipeline` outcome never prompts for it.

---

### Task 2: `axes.js` recognises its own handler and follows `Plotly.react`

**What is wrong.** `isWired` counts ANY `plotly_restyle` listener. In the tool, NiceGUI's plotly element registers its own forwarder on that event synchronously after every `newPlot` (`nicegui/elements/plotly/plotly.js`, `set_handlers`) — before this script's observer runs — so `dstfWireSecondaryAxes` returns early on every node and the axes never follow. Independently, `Plotly.react` (NiceGUI's `update_figure` when the config is unchanged; a drag's snap-back) replaces the layout with the server's — every secondary axis hidden — while `uirevision: "keep"` (the figure layer) keeps a legend-shown trace visible; react fires no `plotly_restyle`, so the axis stays hidden under a visible trace.

**Re-derived, not copied:** plotly's `gd.on` is `gd._ev.on` bound (plotly.min.js 2.35.0, module 68596: `e.on=t.on.bind(t)`), so a handler registered through `gd.on` is in `gd._ev.listeners('plotly_restyle')` by identity; `Plotly.newPlot` on a plotted node runs `Plots.purge`, which ends in `removeAllListeners` and KEEPS `gd._ev` (`Events.init` then reuses it) — so after a re-plot the identity check is false exactly as the count was, and a flag on the node would still lie. `Plotly.react` emits `plotly_react` after every react in 2.35.0 (`emit("plotly_react",{data:r,layout:n})`). NiceGUI's bundled build (`nicegui/elements/plotly/dist/index.js`, `version="3.1.1"`) emits `plotly_react` once and has `listeners` — verified by reading the file, NOT by a test: no test in this plan drives a react in the tool's page, so Task 9 Step 4 probes it by hand.

**Files:**
- Modify: `src/dstf/reporting/templates/axes.js`
- Modify: `tests/test_axes_js_playwright.py`
- Modify: `tests/test_report_suite.py` (delete `test_the_served_script_defines_the_axis_follow_before_the_page_script`, ~1298-1302)
- Modify: `tests/test_interactive_report.py:611-612` (docstring)

- [ ] **Step 1: The page keeps a pristine figure.** In `tests/test_axes_js_playwright.py`'s `page_path` fixture, change the script line `f"<script>const FIG={json.dumps(fig)};"` to:

```python
        f"<script>const FIG={json.dumps(fig)};"
        "const FIG0=JSON.parse(JSON.stringify(FIG));"
```

`Plotly.newPlot` keeps the arrays it is given, so a legend click mutates `FIG.data[i].visible` and the follow's relayout mutates `FIG.layout` in place; a react test needs the server's figure as sent, which `FIG0` now is.

- [ ] **Step 2: The foreign-listener test.** Append to the same file:

```python
def test_a_foreign_restyle_listener_registered_first_does_not_count_as_wired(
    playwright_browser, page_path
):
    """The tool's page: NiceGUI's plotly element registers its own
    plotly_restyle forwarder on every newPlot, before this script's observer
    runs. A wired check that COUNTS listeners sees one and never wires, and
    the axes never follow a legend click in the tool. The check must ask for
    this script's own handler — and wiring twice must not add it twice."""
    page, errors = _open(playwright_browser, page_path)
    page.evaluate(
        """() => { const el = document.getElementById('p');
           Plotly.newPlot('p', FIG.data, FIG.layout, FIG.config);  // purges ours
           el.on('plotly_restyle', function () {});               // foreign, first
           dstfWireSecondaryAxes(el);
           dstfWireSecondaryAxes(el); }"""
    )
    page.wait_for_timeout(200)
    count = page.evaluate(
        "() => document.getElementById('p')._ev.listenerCount('plotly_restyle')"
    )
    _toggle_nrmse_in_legend(page, True)
    after_show = page.evaluate(PROBE)
    page.close()
    assert errors == []
    assert count == 2  # the foreign one and ours, once
    assert after_show["traces"]["error/nrmse"] is True
    assert (after_show["y3"], after_show["y4"]) == (False, True)


def test_the_axis_survives_a_react_that_keeps_the_legend_state(
    playwright_browser, page_path
):
    """Plotly.react with the figure the server holds — every secondary axis
    hidden — is what the tool's update_figure and a drag's snap-back do.
    `uirevision: "keep"` keeps the legend-shown trace visible, but the axis
    flag the follow set lives in the layout and is replaced; react fires no
    plotly_restyle. The follow must also run on plotly_react."""
    page, errors = _open(playwright_browser, page_path)
    _toggle_nrmse_in_legend(page, True)
    assert page.evaluate(PROBE)["y4"] is True
    page.evaluate(
        """() => Plotly.react('p', JSON.parse(JSON.stringify(FIG0.data)),
                              JSON.parse(JSON.stringify(FIG0.layout)), FIG0.config)"""
    )
    page.wait_for_timeout(200)
    after_react = page.evaluate(PROBE)
    page.close()
    assert errors == []
    assert after_react["traces"]["error/nrmse"] is True  # uirevision kept it
    assert (after_react["y3"], after_react["y4"]) == (False, True)
```

- [ ] **Step 3: Run both to see them fail, and read HOW.** `uv run --no-sync pytest -q -m playwright tests/test_axes_js_playwright.py -k "foreign or react"` →
  - the foreign test FAILS at `assert count == 2` (count is 1: the wired check saw the foreign listener and returned);
  - the react test FAILS at `assert (after_react["y3"], after_react["y4"]) == (False, True)` with `y4` False while the trace assertion just above it passed.
  **Contingency:** if the react test instead fails at `after_react["traces"]["error/nrmse"] is True` (the trace came back `legendonly`), `uirevision` did not keep the legend state under a react with a fresh copy, the axis being hidden is then consistent, and the defect does not exist as described: delete the react test and skip the `plotly_react` line in Step 4, and say so in the commit message. Do not weaken the assertion to make it pass.

- [ ] **Step 4: Implement.** Replace `isWired` and `dstfWireSecondaryAxes` in `src/dstf/reporting/templates/axes.js` with:

```js
  function isWired(gd) {
    // Plotly.newPlot on an already-plotted node purges it, which removes
    // every listener (plotly.min.js 2.35.0: Plots.purge ends in
    // removeAllListeners) while the node object — and any flag on it —
    // survives; so ask the emitter whether OUR handler is registered. Not
    // "any listener": the tool's plotly element (NiceGUI) registers its
    // own plotly_restyle forwarder after every newPlot, before this
    // script's observer runs.
    var follow = gd._dstfFollow;
    return !!(follow && gd._ev && typeof gd._ev.listeners === 'function' &&
              gd._ev.listeners('plotly_restyle').indexOf(follow) !== -1);
  }

  function dstfWireSecondaryAxes(gd) {
    if (!gd || typeof gd.on !== 'function' || isWired(gd)) return;
    var follow = function () { dstfFollowSecondaryAxes(gd); };
    gd._dstfFollow = follow;
    gd.on('plotly_restyle', follow);
    // Plotly.react (the tool's update_figure, a drag's snap-back) replaces
    // the layout with the server's — every secondary axis hidden — while
    // uirevision keeps a legend-shown trace visible, and fires no restyle.
    gd.on('plotly_react', follow);
    dstfFollowSecondaryAxes(gd);
  }
```

In the file's header comment, extend the last sentence so it ends `...and writes visible flags — on a restyle (the select, a legend click) and after a react.`

- [ ] **Step 5: Run the file.** `uv run --no-sync pytest -q -m playwright tests/test_axes_js_playwright.py` → all six pass, including `test_the_axis_still_follows_after_a_re_plot_of_the_same_node` (the identity check is false after a purge, as the count was).

- [ ] **Step 6: Delete the test that cannot fail.** In `tests/test_report_suite.py` delete `test_the_served_script_defines_the_axis_follow_before_the_page_script` (report.js's own `dstfWireSecondaryAxes(el);` inside `paintFigure` precedes `function renderAllPlots`, so the reversed part order passes it; the order is not load-bearing — both parts run before `DOMContentLoaded` fires). If it was the last test under a section comment, delete the comment too.

- [ ] **Step 7: Correct the one-axis docstring.** `tests/test_interactive_report.py`, `test_error_overlay_toggle_shows_exactly_one_error_trace`: replace `"""signed / abs / per-point NRMSE, one at a time, on the right-hand axis — which stays hidden while none is shown."""` with `"""signed / abs / per-point NRMSE, one at a time, each on its error axis (signed and abs share the signal-units axis; NRMSE has its own) — an axis is hidden while no series on it is shown."""`.

- [ ] **Step 8: Gate and commit.** The four gates. `git commit -F msg -- src/dstf/reporting/templates/axes.js tests/test_axes_js_playwright.py tests/test_report_suite.py tests/test_interactive_report.py`. Message: `axes.js: the wired check asks for its own handler, and the follow runs after a react — the tool's own restyle listener no longer counts as wired`.

**Mutations the review applies:**
- M1: `isWired` back to `listenerCount('plotly_restyle') > 0` → the foreign test (`count == 1`, axis stays hidden).
- M2: delete `gd.on('plotly_react', follow)` → the react test.
- M3: delete `gd._dstfFollow = follow` → the foreign test's `count == 2` (every wire call adds a handler — the two explicit calls plus the observer's, so 3 or 4; not 2).
- M4: `isWired` returns `!!gd._dstfFollow` (a flag on the node, no emitter check) → `test_the_axis_still_follows_after_a_re_plot_of_the_same_node` (the purge keeps the node's flag; nothing re-wires).

**Done when:** under the report's plotly.js, a node carrying a foreign `plotly_restyle` listener registered before wiring still shows `yaxis4` on a legend click, wiring twice leaves exactly one handler of ours on the emitter, and a `Plotly.react` with the server's figure leaves `yaxis4` visible while the NRMSE trace is.

---

### Task 3: The tool's page carries `axes.js`

The held Task 3 of the foundations plan, unchanged in substance: the two hunks are saved as `.soul/foundations-task3-held.patch` (not tracked). Only its test hunk still applies (Task 0 Step 3); the `app.py` hunk is written by hand below. Its red check failed before AND after the app.py hunk when it was first tried — Task 2 is what makes the hunk work.

**Files:**
- Modify: `src/dstf/authoring/app.py` (`Shell.root`, after `ui.add_css(css_variables())`; the import block)
- Modify: `tests/test_author_smoke_playwright.py`

- [ ] **Step 1: Apply the TEST hunk only.** `git apply --include='tests/*' .soul/foundations-task3-held.patch`. `git status --short` shows only `tests/test_author_smoke_playwright.py`. (If Task 0 Step 3 reported a conflict, add the test by hand: it is `test_a_legend_click_on_an_error_trace_shows_its_axis`, the skeleton of `test_the_real_page_edits_one_tolerance_and_saves_nothing` — `_project`, `_free_port`, the `subprocess.Popen([...])` with `PYTEST_CURRENT_TEST` stripped, `_wait_until_serving`, `new_context().new_page()`, the `finally:` — with this body between `page.goto(url)` and `context.close()`:)

```python
        # The row buttons carry the short name ("A"), inside the tests column.
        tests_column = page.locator("div.dstf-tests")
        tests_column.get_by_role(
            "button", name=A.rsplit(".", 1)[-1], exact=True
        ).click()
        page.wait_for_function(
            "() => { const el = document.querySelector('.js-plotly-plot');"
            " return !!(el && el._fullLayout && el._fullLayout.yaxis4); }",
            timeout=EXPECT_TIMEOUT,
        )
        probe = (
            "() => { const fl = document.querySelector('.js-plotly-plot')._fullLayout;"
            " return [fl.yaxis3.visible, fl.yaxis4.visible]; }"
        )
        assert page.evaluate(probe) == [False, False]
        # axes.js runs in the tool's page too: the legend is its only toggle.
        entry = page.locator(".legend .traces").filter(has_text="NRMSE (per-point)")
        entry.locator(".legendtoggle").click(force=True)
        page.wait_for_function(
            "() => document.querySelector('.js-plotly-plot')._fullLayout.yaxis4.visible === true",
            timeout=EXPECT_TIMEOUT,
        )
        assert page.evaluate(probe) == [False, True]
```

The patch's test registers no page-error listener; add one so the done-check below is asserted, not assumed: directly after `page = context.new_page()` insert

```python
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
```

and directly before `context.close()` insert `assert errors == []`.

- [ ] **Step 2: Run it to see it fail.** `uv run --no-sync pytest -q -m playwright tests/test_author_smoke_playwright.py -k legend_click` → FAILS at the `wait_for_function` on `yaxis4.visible === true` (the page has no `axes.js`; the axis exists and nothing shows it).

- [ ] **Step 3: Write the app.py change by hand** (the patch's hunk no longer applies). In `app.py`'s import block add `from ..reporting.report_suite import axes_js_source` directly BEFORE `from ..reporting.status import classify_with_class` (ruff's isort order: `report_suite` < `status` < `theme`), and in `Shell.root()` directly after `ui.add_css(css_variables())`:

```python
        # The shared axis-follow script (templates/axes.js): its observer
        # wires every plot this page creates. One script for both tiers.
        ui.add_body_html(f"<script>{axes_js_source()}</script>")
```

Confirm `src/dstf/authoring/builtin/edit_on_plot.py` still reads `if handle:` at the top of `on_relayout` (line ~211): the follow's `yaxis4.visible` relayout reaches `FigureHook._dispatch` as a non-shape relayout with an empty handle and must be dropped there, not submitted as an edit.

- [ ] **Step 4: Run.** `uv run --no-sync pytest -q -m playwright tests/test_author_smoke_playwright.py` → all pass. `uv run --no-sync pytest -q tests/test_shell.py tests/test_builtin_capabilities.py tests/test_edit_on_plot.py` → unchanged.

- [ ] **Step 5: The prose that was "true once Task 3 lands" is now true — verify, do not edit.** `axes.js:10` ("the tool adds it to its page body"), `src/dstf/figures/plotly.py:528` ("in both tiers"), `src/dstf/reporting/report_suite.py:44` ("for the tool's page"): each now describes the tree. Read the three lines; if any says something else, correct it in this commit.

- [ ] **Step 6: Gate and commit.** `git commit -F msg -- src/dstf/authoring/app.py tests/test_author_smoke_playwright.py`. Message: `author: the page carries axes.js — a legend click on an error trace shows its axis`. Delete the patch file afterwards (`rm .soul/foundations-task3-held.patch`; untracked, now landed).

**Mutations the review applies:**
- M1: delete the `ui.add_body_html` line → the smoke test.
- M2: keep the line, revert Task 2's `isWired` to the count → the smoke test (NiceGUI's own listener is counted; this is the original defect, reproduced in the real page).

**Done when:** in the served tool page over the FakeLib project, clicking the legend entry "NRMSE (per-point)" makes `_fullLayout.yaxis4.visible` true and leaves `yaxis3` hidden, with no page errors.

---

### Task 4: The Session's cached bases follow the scored list; `figure` refuses an unknown model first

**What is wrong.** `_ModelReads.bases` caches the decimated base view per variable. `view()` builds it from the WHOLE scored list (`comp.variables` when `comp.evaluated`), and that list changes without a results reload: a staged edit that starts or stops scoring a variable, a discard, a save, the background rescan. The cache is keyed on nothing, so `figure()` keeps drawing the old base. Three states reproduce it: stop scoring `y` (the base still draws `y`'s act/ref/errors), start scoring `y` (the base is still empty), and — the one a per-variable key misses — a test with NO baseline where a baseline-free leaf on ANOTHER variable is added: `scored` goes from empty to non-empty, and `x` moves from the no-baseline path (drawn) to the empty state. Also `figure()` calls `_reads_for` before the model check, so `figure("Nope")` caches a read and `figure(D)` after the spec drops D returns the cached figure where `view(D)` raises.

**Re-derived:** the key is the `(name, index)` tuple of the scored list, or `()` when not evaluated — exactly what `build_trajectories` keys its trajectories on (`context_builder.py:305-318`: `vc.name or f"x[{vc.index}]"`, then `vc.index`). Kept on `_ModelReads` and checked in `figure()` rather than cleared eagerly in `_score`, so the cache validates itself whatever writes `comparisons` in future.

**Files:**
- Modify: `src/dstf/authoring/session.py` (`_ModelReads` ~87-96; `figure` ~444-474; `_rebuild_tests` ~698-712; a module-level `_scored_key`)
- Modify: `tests/test_session.py` (`TestReadCache`; a module-level `_two_var_project` helper)

- [ ] **Step 1: The helper and the three stale-base tests.** In `tests/test_session.py`, after `_snapshot`, add:

```python
def _two_var_project(tmp_path: Path, ref_models=(A,)) -> tuple[Path, Path]:
    """`_project` with A and B tracking ["x", "y"], results for both, and a
    two-variable baseline for each model in ``ref_models`` (A only by
    default, so B stays NO_REF)."""
    cfg, work = _project(tmp_path)
    t = [i / 4 for i in range(5)]
    for key in ("test_0001", "test_0002"):
        (work / key / "result.json").write_text(
            json.dumps(
                {
                    "success": True,
                    "time": t,
                    "variables": {"x": [v * 1.25 for v in t], "y": [2 * v for v in t]},
                }
            ),
            encoding="utf-8",
        )
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] in (A, B):
            entry["variables"] = ["x", "y"]
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    config = Config(config_file=cfg, work_dir=work)
    tt = np.asarray(t)
    store = ReferenceStore(config)
    for m in ref_models:
        store.store_reference(
            make_test_model(m, variable_patterns=["x", "y"]),
            TestResult(
                model_id=m,
                success=True,
                variables=[
                    VariableResult(index=1, time=tt, values=tt, name="x"),
                    VariableResult(index=2, time=tt, values=2.1 * tt, name="y"),
                ],
            ),
        )
    return cfg, work


def _base_kinds(fig: dict) -> list[tuple[str, str]]:
    """The base traces a figure draws: (kind, id) of every act / ref / error
    trace, sorted. Leaves are not base traces."""
    return sorted(
        (tr["meta"].get("kind"), tr["meta"].get("id"))
        for tr in fig["data"]
        if tr.get("meta") and tr["meta"].get("kind") in ("act", "ref", "error")
    )
```

and inside `TestReadCache`, after `test_a_staged_edit_keeps_the_cache_and_changes_the_leaves`:

```python
    # A second Session over the same project, opened fresh, is the oracle:
    # what it draws after the same staged op is what the cached one must
    # draw. `_base_kinds` compares base traces only — the leaves are
    # decimated per call and are not what the cache holds.
    @staticmethod
    def _pair(cfg, work):
        s1, s2 = Session(), Session()
        s1.open(cfg, work_dir=work)
        s2.open(cfg, work_dir=work)
        return s1, s2

    def test_a_staged_edit_that_stops_scoring_a_variable_drops_its_base(self, tmp_path):
        cfg, work = _two_var_project(tmp_path)
        cached, fresh = self._pair(cfg, work)
        assert [v.name for v in cached.comparisons[A].variables] == ["x", "y"]
        cached.figure(A, "y")  # y scored: act + ref + errors cached
        op = PatchOp(A, "remove", "/metrics/children/1")
        cached.stage([op])
        fresh.stage([op])
        assert [v.name for v in cached.comparisons[A].variables] == ["x"]
        assert _base_kinds(cached.figure(A, "y")) == _base_kinds(fresh.figure(A, "y"))

    def test_a_staged_edit_that_starts_scoring_a_variable_rebuilds_its_base(
        self, tmp_path
    ):
        cfg, work = _two_var_project(tmp_path)
        cached, fresh = self._pair(cfg, work)
        remove = PatchOp(A, "remove", "/metrics/children/1")
        cached.stage([remove])
        fresh.stage([remove])
        cached.figure(A, "y")  # y unscored: an empty base cached
        add = PatchOp(A, "add", "/metrics/children/-", {"metric": "nrmse", "variable": "y"})
        cached.stage([add])
        fresh.stage([add])
        assert [v.name for v in cached.comparisons[A].variables] == ["x", "y"]
        assert _base_kinds(cached.figure(A, "y")) == _base_kinds(fresh.figure(A, "y"))

    def test_a_baseline_free_leaf_on_another_variable_moves_an_unscored_one(
        self, tmp_path
    ):
        """B has no baseline: with nothing scored, x is drawn through the
        no-baseline path (act only). A tree scoring ONLY y (a baseline-free
        range) makes the scored list non-empty and x — still unscored —
        becomes the empty state. A key on "is x scored" does not move; the
        key on the whole scored list does."""
        cfg, work = _two_var_project(tmp_path)
        cached, fresh = self._pair(cfg, work)
        assert not cached.comparisons[B].evaluated
        cached.figure(B, "x")
        op = PatchOp(
            B,
            "add",
            "/metrics",
            {
                "combinator": "and",
                "children": [
                    {"metric": "range", "variable": "y", "min_value": -10, "max_value": 10}
                ],
            },
        )
        cached.stage([op])
        fresh.stage([op])
        assert cached.comparisons[B].evaluated
        assert _base_kinds(cached.figure(B, "x")) == _base_kinds(fresh.figure(B, "x"))

    def test_figure_of_an_unknown_model_is_a_key_error_that_caches_nothing(
        self, session
    ):
        with pytest.raises(KeyError):
            session.figure("FakeLib.Nope", "x")
        assert "FakeLib.Nope" not in session._reads

    def test_a_model_the_spec_drops_loses_its_cached_read(self, session):
        session.figure(D, "x")
        p = session.spec.path
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc["tests"] = [e for e in doc["tests"] if e["model"] != D]
        p.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        session.reload()
        assert D not in session._reads
        with pytest.raises(KeyError):
            session.figure(D, "x")
```

- [ ] **Step 2: Run them to see them fail.** `uv run --no-sync pytest -q tests/test_session.py -k "TestReadCache and (stops_scoring or starts_scoring or baseline_free_leaf or unknown_model or spec_drops)"` → five FAIL: the three staleness tests at their `_base_kinds` equality (the cached side still lists — or still lacks — the act/ref/error traces); the unknown-model test at `"FakeLib.Nope" not in session._reads`; the dropped-model test at `D not in session._reads` (and `figure(D, "x")` returns a figure instead of raising).

- [ ] **Step 3: Implement.** In `src/dstf/authoring/session.py`:

`_ModelReads` becomes:

```python
@dataclass
class _ModelReads:
    """What one click re-read from disk before this cache (brief §2.2): the
    primary reference and the overlays; plus the decimated base view per
    variable — trajectories, overlays and error series, no leaves — so an
    edit re-decimates only the criterion's geometry."""

    ref: dict | None
    overlays: list
    bases: dict[str, VariableView] = field(default_factory=dict)
    #: The scored list the bases were built from (`_scored_key`). A rescore
    #: that changes it — a staged edit that starts or stops scoring a
    #: variable, a discard, a save, the background rescan — makes every
    #: base of the model stale at once; `figure` drops them when it moves.
    scored: tuple[tuple[str | None, int], ...] = ()


def _scored_key(comp: TestComparison | None) -> tuple[tuple[str | None, int], ...]:
    """What `view` hands `build_trajectories`, reduced to what the base
    traces depend on: the (name, index) of every scored variable, in order;
    empty when the test was not evaluated. The WHOLE list, not "is this
    variable in it": with no baseline, an empty scored list draws a variable
    through the no-baseline path and a non-empty one (a baseline-free leaf
    on ANOTHER variable) draws it as the empty state."""
    # `evaluated` is `metric_tree is not None or bool(variables)`
    # (comparison/types.py), so an unevaluated comparison has no variables
    # and the tuple is empty by construction — no separate check.
    if comp is None:
        return ()
    return tuple((vc.name, vc.index) for vc in comp.variables)
```

`figure()`'s opening becomes:

```python
        project = self._require_project()
        if project.test(model_id) is None:
            raise KeyError(model_id)  # before the cache: never cache a read for it
        budget = project.config.max_embedded_samples
        reads = self._reads_for(model_id)
        scored = _scored_key(self.comparisons.get(model_id))
        if reads.scored != scored:
            reads.bases.clear()
            reads.scored = scored
        base = reads.bases.get(variable)
```

(the rest unchanged). In `_rebuild_tests`, the `gone` loop becomes:

```python
        for model_id in gone:
            del self.comparisons[model_id]
            self._reads.pop(model_id, None)
```

- [ ] **Step 4: Run the class and the file.** `uv run --no-sync pytest -q tests/test_session.py` → all pass, including `test_a_second_figure_decimates_only_the_leaves` (the key does not move between two figures of one state) and `test_the_cached_figure_is_the_one_shot_figure_under_decimation`.

- [ ] **Step 5: Gate and commit.** `git commit -F msg -- src/dstf/authoring/session.py tests/test_session.py`. Message: `session: the cached bases follow the scored list; figure refuses an unknown model before it caches`.

**Mutations the review applies:**
- M1: never compare the key (`if False:`) → the three staleness tests.
- M2: key on `bool(scored)` (non-empty vs empty) → the stop-scoring and start-scoring tests (x stays scored in both states; the list changes, its truth does not).
- M3: key on `tuple(vc.name for vc in comp.variables)` (names only, no index) → must SURVIVE the plan's tests: no fixture scores two variables of one name with different indices. The reviewer confirms it survives and records it; the index stays in the key because `build_trajectories` resolves a variable by `vc.index` when it is positive (`context_builder.py:313-315`).
- M4: keep `reads.bases.clear()` but drop `reads.scored = scored` → `test_a_second_figure_decimates_only_the_leaves` (every figure re-decimates the base).
- M5: move the `KeyError` after `_reads_for` → the unknown-model test.
- M6: drop the `_reads.pop` in `_rebuild_tests` → the dropped-model test's `D not in session._reads`.

**Done when:** for each of the three scored-list transitions, `_base_kinds(cached.figure(...))` equals a fresh Session's after the same op; `figure` of an unknown model raises `KeyError` and leaves `_reads` untouched; a model the spec drops is gone from `_reads` after `reload`.

---

### Task 5: The rescan re-reads a changed model's result before rescoring it

**What is wrong.** A cached open reads results with the CACHED tests. A model added since the cache was written (`Bar.mo` added, then `dstf run`) is read as "Test model not found in discovery"; the rescan then adds it to `project.tests` and rescores it against that failed result — SIM_FAIL where a cold open says NO_REF — until a job or a store write reloads. A source edit to the tracked variables (`n=`/`x={...}`) is the same defect one step later: the result was read with the old variable list. The rescan must re-read the changed models with their new `TestModel`s before it rescores them.

**Files:**
- Modify: `src/dstf/authoring/session.py` (`_rescan`'s `apply` ~313-330; `_rebuild_tests` ~698-712; a new `_reread_results`)
- Modify: `tests/test_session.py` (`TestOpen`)

- [ ] **Step 1: The added-model test and the three guard tests.** In `tests/test_session.py`, after `_bump_stop_time`, add:

```python
def _add_bar(tmp_path: Path) -> None:
    """A second model in the Modelica library `_modelica_project` writes."""
    (tmp_path / "MyLib" / "Bar.mo").write_text(
        "within MyLib;\nmodel Bar\n  Real y;\n  UnitTests unitTests(n=1, x={y});\n"
        "equation\n  y = time;\n  annotation(experiment(StopTime=1));\nend Bar;\n",
        encoding="utf-8",
    )
```

and inside `TestOpen`, after `test_a_source_edit_reaches_the_project_through_the_rescan`:

```python
    def test_a_model_added_since_the_cache_reads_its_result_before_it_is_scored(
        self, tmp_path, monkeypatch
    ):
        """The cache names Foo only; `dstf run` has since simulated Foo and
        Bar. The stale open reads Bar as "not found in discovery"; the
        rescan must re-read it with its TestModel before rescoring, or Bar
        scores SIM_FAIL where a cold open says NO_REF."""
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        cfg = _modelica_project(tmp_path)
        work = tmp_path / "work"
        Session().open(cfg, work_dir=work)  # the cache names Foo only
        _add_bar(tmp_path)
        work.mkdir(exist_ok=True)
        manifest = {
            "test_0001": {"model_id": "MyLib.Foo", "ref_id": None, "last_run_status": "passed"},
            "test_0002": {"model_id": "MyLib.Bar", "ref_id": None, "last_run_status": "passed"},
        }
        (work / "batch_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        for key in manifest:
            (work / key).mkdir()
            (work / key / "result.json").write_text(
                json.dumps({"success": True, "time": [0, 1], "variables": {}}),
                encoding="utf-8",
            )
        stale = Session()
        stale.open(cfg, work_dir=work)
        assert stale.rescan_done.wait(10.0)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cold"))
        cold = Session()
        cold.open(cfg, work_dir=work)
        assert classify(stale.comparisons["MyLib.Bar"]) == classify(
            cold.comparisons["MyLib.Bar"]
        )
        assert stale.project.results["MyLib.Bar"].success is True

    def test_only_the_changed_model_of_two_is_rescored(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        cfg = _modelica_project(tmp_path)
        _add_bar(tmp_path)
        Session().open(cfg, work_dir=tmp_path / "work")
        _mo, before = _bump_stop_time(tmp_path)
        s = Session()
        rescored = []
        s.on("results", lambda e: rescored.append(set(e["models"])))
        s.open(cfg, work_dir=tmp_path / "work")
        assert {"MyLib.Foo", "MyLib.Bar"} <= set(s.comparisons)
        assert s.rescan_done.wait(10.0)
        assert s.project.test(before.model_id).stop_time == before.stop_time + 1
        assert rescored[-1] == {before.model_id}

    def test_done_precedes_rescan_even_when_the_rescan_wins_the_race(
        self, tmp_path, monkeypatch
    ):
        """The worst schedule: the rescan thread finishes inside `start()`.
        `done` is emitted before the thread starts, so it still comes first."""
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        cfg = _modelica_project(tmp_path)
        Session().open(cfg, work_dir=tmp_path / "work")
        real_start = threading.Thread.start

        def start(self):
            if self.name == "dstf-rescan":
                self.run()
            else:
                real_start(self)

        monkeypatch.setattr(threading.Thread, "start", start)
        s = Session()
        events = []
        s.on("loading", events.append)
        s.open(cfg, work_dir=tmp_path / "work")
        assert s.rescan_done.is_set()
        phases = [e["phase"] for e in events]
        assert phases.index("done") < phases.index("rescan")

    def test_a_locked_model_is_left_to_its_job_by_the_rescan(self, tmp_path, monkeypatch):
        import queue

        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        cfg = _modelica_project(tmp_path)
        Session().open(cfg, work_dir=tmp_path / "work")
        _mo, before = _bump_stop_time(tmp_path)
        posted: queue.Queue = queue.Queue()
        s = Session(post=posted.put)
        s.open(cfg, work_dir=tmp_path / "work")
        comp = s.comparisons[before.model_id]
        release = threading.Event()
        s.apply(
            Job(
                run=lambda ctx: release.wait(10),
                label="rerun",
                models=frozenset({before.model_id}),
            )
        )
        rescored = []
        s.on("results", lambda e: rescored.append(set(e["models"])))
        posted.get(timeout=10)()  # the rescan's re-merge, run on "the UI loop"
        assert s.rescan_done.is_set()
        assert s.comparisons[before.model_id] is comp  # not rescored under the lock
        assert all(before.model_id not in r for r in rescored)
        release.set()
        posted.get(timeout=10)()  # the job's completion
        assert s.running is None
```

`classify` and `Job` are already imported at the top of the file.

- [ ] **Step 2: Run them.** `uv run --no-sync pytest -q tests/test_session.py -k "added_since_the_cache or changed_model_of_two or wins_the_race or left_to_its_job"` → the added-model test FAILS at the `classify` equality (`SIM_FAIL` vs `NO_REF`); the other three PASS on the unchanged code — they are guards, and each has its mutation below.

- [ ] **Step 3: Implement.** In `src/dstf/authoring/session.py`, `_rescan`'s `apply` body inside the `if changed_models and self.project is project:` block becomes:

```python
                    project.scanned = fresh
                    # Only what changed is rescored: a one-file edit over
                    # TRANSFORM must not run 331 comparisons on the UI loop.
                    # A model a running job has locked is left to that job's
                    # completion reload, as `stage` and `rescore` leave it.
                    # The changed models' results are re-read with their new
                    # TestModels before they are rescored: a model the cache
                    # did not name was read as "not found in discovery"
                    # (SIM_FAIL where a cold open says NO_REF), and a model
                    # whose tracked variables changed was read with the old
                    # list.
                    locked = (
                        self.running.models if self.running is not None else frozenset()
                    )
                    self._rebuild_tests(changed_models - locked, reread=True)
```

`_rebuild_tests` becomes:

```python
    def _rebuild_tests(self, models: set[str], *, reread: bool = False) -> None:
        """Re-merge the scan against the effective spec; models the spec no
        longer names drop out of ``comparisons``, models it newly names are
        scored (with whatever result the last reload holds for them). With
        ``reread`` the ``models`` are first re-read from the last run with
        their merged TestModels (the rescan's case)."""
        project = self._require_project()
        project.tests = self._merge(
            project.config, project.scanned, self.spec.effective()
        )
        current = {t.model_id for t in project.tests}
        gone = set(self.comparisons) - current
        new = current - set(self.comparisons)
        for model_id in gone:
            del self.comparisons[model_id]
            self._reads.pop(model_id, None)
        if reread:
            self._reread_results(models & current)
        self._emit("spec", models=models | gone | new)
        self._score_all((models | new) & current)

    def _reread_results(self, models: set[str]) -> None:
        """Re-read ``models`` from the last run with their CURRENT
        TestModels, dropping what an earlier read stored for them. Only
        they are stored: ``read_results`` reports every manifest entry, and
        the others are not being re-read. A model with no manifest entry
        ends with no result and is scored NO_REF / SIM_FAIL by the caller,
        as after a full reload."""
        project = self._require_project()
        for model_id in models:
            project.results.pop(model_id, None)
            self._reads.pop(model_id, None)
        to_read = [t for t in project.tests if t.model_id in models]

        def on_result(model_id: str, result: TestResult) -> None:
            if model_id in models:
                project.results[model_id] = result

        project.backend.read_last_results(to_read, on_result=on_result)
```

(The `gone` loop's `_reads.pop` is Task 4's; keep it.)

- [ ] **Step 4: Run the file.** `uv run --no-sync pytest -q tests/test_session.py` → all pass. Run the four new tests ten times for flakes: `uv run --no-sync pytest -q tests/test_session.py -k "added_since_the_cache or changed_model_of_two or wins_the_race or left_to_its_job" --count 10` if `pytest-repeat` is present, else a shell `for i in $(seq 10)` loop reading each summary line.

- [ ] **Step 5: Gate and commit.** `git commit -F msg -- src/dstf/authoring/session.py tests/test_session.py`. Message: `session: the rescan re-reads a changed model's result with its new TestModel before rescoring it`.

**Mutations the review applies:**
- M1: `reread=False` in the rescan's call → the added-model test.
- M2: `_rebuild_tests(changed_models)` — rescore all instead of changed minus locked — is two mutations: (a) `set(fresh)` instead of `changed_models` → `test_only_the_changed_model_of_two_is_rescored`; (b) drop `- locked` → `test_a_locked_model_is_left_to_its_job_by_the_rescan`.
- M3: emit `done` after the rescan thread starts → `test_done_precedes_rescan_even_when_the_rescan_wins_the_race`.
- M4: re-read AFTER `_score_all` → the added-model test (scored against the stale result; the result is then replaced, the verdict is not).
- M5: drop `if model_id in models` in `_reread_results` → `test_the_rescan_stores_only_the_models_it_re_read` (added at the build's review, 2026-09-16). The plan first called this filter unobservable — wrong: `read_results` builds its lookup from the re-read's own list, so EVERY other manifest model, in the spec or not, comes back "not found in discovery" and an unchanged model's next rescore says SIM_FAIL. The review executed the mutant and showed it.

**Done when:** a stale cached open over a work dir that ran a model the cache did not name gives that model the verdict a cold open gives, its result stored as the run left it; only the changed models are rescored; a locked one is not; `done` precedes `rescan` under the worst schedule.

---

### Task 6: `store_scan` cannot fail the open, and its temporary is per thread

**What is wrong.** `store_scan` raises `OSError` unguarded: an unwritable cache home fails a cold `Session.open` (`dstf author` does not open), and on a cached open whose rescan found a change the rescan thread dies in `store_scan` — `rescan_done` is never set, no `rescan` event, the edit never lands. Its temporary is `<key>.<pid>.tmp`, shared by two rescan threads (or two Sessions) in one process: the second write replaces the first's file from under it and its own `os.replace` raises `FileNotFoundError`.

**Files:**
- Modify: `src/dstf/discovery/scan_cache.py` (`store_scan` ~86-102; `import threading`)
- Modify: `tests/test_scan_cache.py`
- Modify: `tests/test_session.py` (`TestOpen`)

- [ ] **Step 1: The unit tests.** In `tests/test_scan_cache.py` add `import os`, `import threading`, `import logging` to the imports, and append:

```python
def test_an_unwritable_cache_dir_is_a_warning_and_stores_nothing(
    tmp_path, monkeypatch, caplog
):
    """The cache is an accelerator: a home it cannot be written to (here the
    `dstf` segment is a FILE, so `mkdir -p` raises NotADirectoryError, an
    OSError) is logged and skipped, never raised at whoever wanted a scan."""
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "dstf").write_text("not a directory", encoding="utf-8")
    config = _write_lib(tmp_path)
    scanned = scan_source_tests(config)
    with caplog.at_level(logging.WARNING, logger="dstf.discovery.scan_cache"):
        assert scan_cache.store_scan(config, scanned) is None
    assert any("not caching the scan" in r.getMessage() for r in caplog.records)
    assert scan_cache.load_scan(config) is None


def test_two_threads_can_store_the_same_key_at_once(tmp_path, monkeypatch):
    """A cached open's rescan thread and a second Session's in one process
    write the same key. Both are held at the rename until both temporaries
    exist: with a per-process temporary name the second write replaced the
    first thread's file from under it and its own rename raised
    FileNotFoundError (which the OSError guard would turn into a silent
    None — either way, not the path)."""
    config = _write_lib(tmp_path)
    scanned = scan_source_tests(config)
    gate = threading.Barrier(2, timeout=10)
    real_replace = os.replace

    def held_replace(src, dst):
        gate.wait()
        return real_replace(src, dst)

    monkeypatch.setattr(scan_cache.os, "replace", held_replace)
    outcomes: list = []

    def store():
        try:
            outcomes.append(scan_cache.store_scan(config, scanned))
        except BaseException as e:  # a failure is an outcome too
            outcomes.append(e)

    threads = [threading.Thread(target=store) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert outcomes == [scan_cache._path(config)] * 2
    assert scan_cache.load_scan(config) == scanned
    assert not list(scan_cache.cache_dir().glob("*.tmp"))
```

- [ ] **Step 2: Run them to see them fail.** `uv run --no-sync pytest -q tests/test_scan_cache.py -k "unwritable or two_threads"` → the first ERRORS with `NotADirectoryError` out of `store_scan` (nothing guards it); the second FAILS at `outcomes == [...] * 2` with a `FileNotFoundError` as one outcome.

- [ ] **Step 3: The Session tests.** In `tests/test_session.py`, `TestOpen`, after `test_open_writes_nothing_in_the_work_dir_even_when_it_caches`:

```python
    def test_a_cold_open_that_cannot_cache_still_opens(self, tmp_path, monkeypatch):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "dstf").write_text("not a directory", encoding="utf-8")
        monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
        cfg = _modelica_project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=tmp_path / "work")
        assert "MyLib.Foo" in s.comparisons
        assert s.rescan_done.is_set()  # a cold open has nothing to rescan

    def test_a_rescan_that_cannot_cache_still_re_merges_and_finishes(
        self, tmp_path, monkeypatch
    ):
        """The cache was written by a cold open and is readable; the rescan
        finds a change and cannot write it back. The re-merge and the
        `rescan` event must still happen and `rescan_done` must be set."""
        import os
        import stat

        cache = tmp_path / "cache"
        monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
        cfg = _modelica_project(tmp_path)
        Session().open(cfg, work_dir=tmp_path / "work")
        from dstf.discovery.scan_cache import cache_dir

        scan_dir = cache_dir()
        assert list(scan_dir.glob("*.json"))
        _mo, before = _bump_stop_time(tmp_path)
        scan_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            if os.access(scan_dir, os.W_OK):
                pytest.skip("this user can write a read-only directory (root)")
            s = Session()
            events = []
            s.on("loading", events.append)
            s.open(cfg, work_dir=tmp_path / "work")
            assert s.rescan_done.wait(10.0)
            assert events[-1]["phase"] == "rescan" and events[-1]["changed"] is True
            assert s.project.test(before.model_id).stop_time == before.stop_time + 1
        finally:
            scan_dir.chmod(stat.S_IRWXU)
```

- [ ] **Step 4: Run them to see them fail.** `uv run --no-sync pytest -q tests/test_session.py -k "cannot_cache"` → the cold-open test ERRORS with `NotADirectoryError` out of `open`; the rescan test FAILS at `assert s.rescan_done.wait(10.0)` (the thread died in `store_scan`; a `Thread` exception warning is in the log). If the rescan test SKIPS, the build session is root: report it in the handoff, do not remove the test.

- [ ] **Step 5: Implement.** In `src/dstf/discovery/scan_cache.py` add `import threading` and replace `store_scan`:

```python
def store_scan(config: Config, scanned: dict[str, TestModel]) -> Path | None:
    """Write the scan; returns the path, or None for a non-Modelica source
    or when the cache cannot be written (a read-only home, a full disk).
    The cache is an accelerator: a failed write is a warning, never an
    error — an open that cannot cache still opens, and a rescan that cannot
    cache still re-merges and finishes."""
    if config.source_type != "modelica":
        return None
    path = _path(config)
    payload = {
        "dstf_version": __version__,
        "library_dir": str(config.library_dir.resolve()),
        "tests": {m: _to_json(t) for m, t in scanned.items()},
    }
    # Unique per thread as well as per process: a cached open's rescan
    # thread and a second Session's in the same process write the same
    # key, and with one name the second write replaced the first's file
    # from under it and its own rename failed.
    tmp = path.with_name(f"{path.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("not caching the scan at %s: %s", path, e)
        return None
    return path
```

- [ ] **Step 6: Run.** `uv run --no-sync pytest -q tests/test_scan_cache.py tests/test_session.py` → all pass (the rescan test may skip as root; see Step 4).

- [ ] **Step 7: Gate and commit.** `git commit -F msg -- src/dstf/discovery/scan_cache.py tests/test_scan_cache.py tests/test_session.py`. Message: `scan cache: an unwritable cache dir is a warning, not a failed open; the temporary is named per thread`.

**Mutations the review applies:**
- M1: remove the `try/except OSError` → the unwritable-dir unit test, the cold-open test, the rescan test.
- M2: catch the error but return `path` → the unit test's `store_scan(...) is None`.
- M3: `.{os.getpid()}.tmp` (drop the thread ident) → the two-threads test.
- M4: catch `Exception` instead of `OSError` — must SURVIVE the tests; the reviewer records it as a widening nobody asked for, and the implementer keeps `OSError`: a `ValueError` from `_to_json` (a scanned test carrying a metric tree) is a programming error and must raise.

**Done when:** `store_scan` over an unwritable cache dir returns `None` and logs a warning; a cold `Session.open` over that dir opens; a cached open whose rescan finds a change and cannot write it still re-merges, emits `rescan`, and sets `rescan_done`; two threads storing one key both return the path and leave no `.tmp` behind.

---

### Task 7: `_reload_results` announces every scored test, and one model's scoring failure leaves the results complete

**What is wrong.** (1) A discovered test with no manifest entry (added to the spec since the last run) is scored after the per-result loop but never announced as `loading(phase="results")`; `total` counts it, so `done` never reaches `total` and a Suite page filling rows per event gets no row for it until the batch event. (2) An exception from `_score` inside `on_result` unwinds `read_results` mid-way: `project.results` holds whichever subset had landed, `comparisons` keeps the pre-reload verdicts for the rest, and no `results` event follows. Reachable via `_finish_job` (which catches and records `job.error`) and via `open`.

**Files:**
- Modify: `src/dstf/authoring/session.py` (`_reload_results` ~739-781)
- Modify: `tests/test_session.py` (`TestOpen`)

- [ ] **Step 1: The tests.** In `tests/test_session.py`, `TestOpen`, after `test_a_test_is_scored_when_its_loading_event_fires`:

```python
    def test_a_test_added_since_the_last_run_is_announced_and_done_reaches_total(
        self, tmp_path
    ):
        """FakeLib.E is in the spec and has no manifest entry: it is scored
        after the per-result loop (SIM_FAIL, no result). It must be
        announced like the others — a page filling rows per event has no
        row for it otherwise — and the last announcement is done == total."""
        cfg, work = _project(tmp_path)
        e_id = "FakeLib.E"
        (cfg.parent.parent / "Examples" / "E.py").write_text(
            "def simulate(stop_time, tolerance):\n    return {}\n", encoding="utf-8"
        )
        spec_path = cfg.parent / "test_spec.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec["tests"].append(
            {
                "model": e_id,
                "source": "../Examples/E.py",
                "variables": ["x"],
                "simulation": {"stop_time": 1.0, "tolerance": 1e-6},
            }
        )
        spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        s = Session()
        events = []
        s.on("loading", events.append)
        s.open(cfg, work_dir=work)
        announced = [e for e in events if e["phase"] == "results"]
        assert {e["model"] for e in announced} == {A, B, C, D, G, e_id}
        assert [e["done"] for e in announced] == list(range(1, 7))
        assert announced[-1]["done"] == announced[-1]["total"] == 6
        assert classify(s.comparisons[e_id]) == SIM_FAIL

    def test_one_models_scoring_failure_leaves_the_results_complete_and_is_raised(
        self, session, monkeypatch
    ):
        """A scorer that raises for one model (a malformed reference, a
        scorer bug) must not abort the read: every result is stored, every
        model is announced, the batch event fires, the failed model's stale
        verdict is dropped rather than shown, and the failure is raised once
        — `_finish_job` records it as the job's error; `open` propagates it."""
        import dstf.authoring.session as session_mod

        real = session_mod.compare_one

        def failing(test, *args, **kwargs):
            if test.model_id == D:
                raise ValueError("boom")
            return real(test, *args, **kwargs)

        monkeypatch.setattr(session_mod, "compare_one", failing)
        events = []
        session.on("loading", events.append)
        session.on("results", lambda e: events.append({"event": "results", **e}))
        with pytest.raises(RuntimeError) as exc_info:
            session._reload_results()
        assert isinstance(exc_info.value.__cause__, ValueError)
        assert D in str(exc_info.value)
        assert {A, B, C, D, G} <= set(session.project.results)
        assert D not in session.comparisons
        assert classify(session.comparisons[A]) == FAIL
        announced = [e for e in events if e.get("phase") == "results"]
        assert announced[-1]["done"] == announced[-1]["total"] == 5
        assert events[-1]["event"] == "results"
```

`SIM_FAIL`, `FAIL` and `classify` are imported at the top of the file.

- [ ] **Step 2: Run them to see them fail.** `uv run --no-sync pytest -q tests/test_session.py -k "added_since_the_last_run or scoring_failure"` → the first FAILS at `{e["model"] for e in announced} == {...}` (E is missing; the last `done` is 5 of 6); the second FAILS at `pytest.raises(RuntimeError)` (a bare `ValueError` unwinds out of `read_results`; `D` is still in `comparisons` with its old verdict).

**What the callers do with the raise — specified, not left to the build:** `_finish_job` catches it (`except BaseException` → `job.error`), so a rerun whose scoring failed for one model reports as `failed` with the raise's message, and `job.reloaded` stays False so the panel shows no rows for it — intended: the failure must be seen, and the complete `comparisons` are still what every other surface reads. `open` propagates it as it propagates the bare exception today: the project is set and scored complete-minus-one, but `loading(phase="done")` never fires and `rescan_done` is left as it was (the Event from `__init__`, never set, or the previous open's) — `dstf author` then fails to start, which is the existing behaviour for a scoring failure and not this task's to change. Say both in the docstring as written below; do not "fix" either.

- [ ] **Step 3: Implement.** `_reload_results` becomes:

```python
    def _reload_results(self) -> None:
        """Re-read the last run and rescore — every model except the ones a
        running job has locked (their directories are being rewritten, so
        their in-memory result and verdict stand until that job's own
        completion reload). Each result is scored as it is read and
        announced as ``loading(phase="results")``; tests with no result are
        scored and announced after the read, so the last announcement is
        ``done == total``; ONE ``results`` event follows for the batch. A
        model whose scoring raises does not abort the read: the results stay
        complete, its stale verdict is dropped, and the first failure is
        raised after the batch event — ``_finish_job`` records it as the
        job's error (no rows shown for a failed job); ``open`` propagates
        it, with no ``done`` event and ``rescan_done`` untouched."""
        project = self._require_project()
        locked = self.running.models if self.running is not None else frozenset()
        to_read = [t for t in project.tests if t.model_id not in locked]
        project.results = {m: r for m, r in project.results.items() if m in locked}
        for model_id in list(self._reads):
            if model_id not in locked:
                del self._reads[model_id]
        announced: set[str] = set()
        failures: list[tuple[str, Exception]] = []

        def score_and_announce(model_id: str) -> None:
            try:
                self._score(model_id)
            except Exception as e:
                self.comparisons.pop(model_id, None)
                failures.append((model_id, e))
            if model_id in announced:
                return  # one model under two manifest keys is read twice
            announced.add(model_id)
            self._emit(
                "loading",
                phase="results",
                model=model_id,
                done=len(announced),
                total=len(to_read),
            )

        def on_result(model_id: str, result: TestResult) -> None:
            if model_id in locked:
                # `read_results` reports every manifest entry, and a locked
                # model — excluded from `to_read` — comes back as "not found
                # in discovery": storing it would rescore the model to
                # SIM_FAIL in the middle of its own job.
                return
            project.results[model_id] = result
            if project.test(model_id) is not None:
                score_and_announce(model_id)

        project.backend.read_last_results(to_read, on_result=on_result)
        # Tests with no result at all (never run, or dropped from the
        # manifest) are scored now — NO_REF / SIM_FAIL states.
        for test in to_read:
            if test.model_id not in project.results:
                score_and_announce(test.model_id)
        self._emit("results", models={t.model_id for t in to_read})
        if failures:
            model_id, error = failures[0]
            raise RuntimeError(
                f"scoring {model_id} failed ({len(failures)} of {len(to_read)} models)"
            ) from error
```

- [ ] **Step 3b: The lock excludes the locked model from `total`.** In `tests/test_session.py`, `TestLockAndCompletion::test_a_reload_during_a_job_keeps_the_locked_model_as_it_was`, register a `loading` listener before the `StoreWrite` and assert on it after:

```python
        events = []
        session.on("loading", events.append)
        session.apply(StoreWrite(run=lambda store: None, models=frozenset({B})))
        # A is locked: to_read is B, C, D, G — four announcements, total 4.
        assert [e["total"] for e in events if e.get("phase") == "results"] == [4] * 4
```

(the `session.apply(StoreWrite(...))` line already exists; the listener goes before it, the assertion after). Neither the unchanged code nor Step 3's fails it as written; it exists for mutation M6 (`total=len(project.tests)` gives 5), which the review applies.

- [ ] **Step 4: Run the file.** `uv run --no-sync pytest -q tests/test_session.py` → all pass, including `test_open_reports_its_phases_and_every_scored_test` (its `done <= total` holds) and `TestLockAndCompletion`.

- [ ] **Step 5: Gate and commit.** `git commit -F msg -- src/dstf/authoring/session.py tests/test_session.py`. Message: `session: every scored test is announced, and one model's scoring failure leaves the results complete`.

**Mutations the review applies:**
- M1: the post-loop `for` calls `self._score` directly (no announcement) → the added-test test.
- M2: drop the `try/except` around `_score` → the failure test (`ValueError` unwinds, `D` keeps its verdict).
- M3: keep the guard but do not `pop` the failed comparison → `D not in session.comparisons`.
- M4: keep the guard, never raise → `pytest.raises(RuntimeError)`.
- M5: raise BEFORE the batch `results` event → `events[-1]["event"] == "results"`.
- M6: `total=len(project.tests)` → Step 3b's assertion in the locked-reload test (total excludes the locked model).

**Done when:** over a project whose spec names a model the manifest does not, `open` announces every model in `to_read` with the last announcement `done == total`; with a scorer raising for one model, `_reload_results` raises `RuntimeError` from it AFTER storing every result, announcing every model and emitting the batch event, and that model has no comparison.

---

### Task 8: Documents — D128 and the roadmap's status block

**Files:**
- Modify: `docs/decisions.md` (append D128 after D127)
- Modify: `docs/superpowers/plans/2026-09-15-authoring-tool-redesign-roadmap.md` (~112-135, the "Status (built 2026-09-15, not yet gated)" block)

- [ ] **Step 1: D128.** Append to `docs/decisions.md`:

```markdown
## D128 — The run verdict is applied in the one reader (2026-09-16)

`Backend.read_results` applies the batch manifest's `last_run_status` (via
`run_verdicts`) to every result before storing it and before its `on_result`
callback; `read_last_results` is that reader over the manifest on disk. Until
now only `read_last_results` applied the verdict, and `run_pipeline` read back
through `read_results` directly — so `run --merge` and `--rerun`, whose
merged-in models have no in-memory run result, reported PASS for a test whose
run had timed out while `compare` on the same work dir reported SIM_FAIL, and
`run --rerun failed -i` then offered that run's partial `.mat` for acceptance
as the primary baseline. The guard lives at the read because a call-site
patch leaves a second place to forget; `tests/test_pipeline.py` drives the
reader and the merged pipeline, `tests/test_cli.py` the review loop over a
merged outcome.

The partial-rerun flip over OLD data noted in `docs/ideas.md` (2026-09-14)
is a different cause — `status.json`'s subset snapshot losing rows for
manifest entries written before `last_run_status` existed — and stays open.
```

- [ ] **Step 2: The roadmap's status block.** Replace the paragraph beginning `**Status (built 2026-09-15, not yet gated).**` and its bullet list (up to, not including, `Gate over TRANSFORM so far:`) with:

```markdown
**Status (built 2026-09-15; corrections landed 2026-09-<DD>).** Tasks 0–2 and
4–10 landed with their fresh-context reviews' test and wording folds; the
held items landed as `docs/superpowers/plans/2026-09-16-foundations-corrections.md`,
one commit and one review each: Task 3 with `axes.js` asking the emitter for
its own handler and following `Plotly.react`; the Session's cached bases keyed
on the scored list; the rescan re-reading a changed model with its new
TestModel; a scan-cache write that can fail without failing the open; every
scored test announced and one model's scoring failure leaving the results
complete. Beside them, a pre-existing run-path bug (D128): the run verdict is
now applied in `Backend.read_results`, so `run --merge` / `--rerun` report a
timed-out run as `compare` does. Still open, and not sub-project 1's: the
tool's body keeps Quasar's font stack until sub-project 2's shell applies the
token, and the click's 1.2 s is the current shell's.
```

with `<DD>` the day the build session commits it. Then replace `Gate over TRANSFORM so far:` with `Gate over TRANSFORM (2026-09-<DD>):` and update its sentences from Task 9's numbers (the tool half of the axis predicate now passes; the timings re-measured).

- [ ] **Step 3: Commit** (after Task 9 has produced the numbers Step 2 quotes — this task's commit is the plan's last): `git commit -F msg -- docs/decisions.md docs/superpowers/plans/2026-09-15-authoring-tool-redesign-roadmap.md`. Message: `docs: D128 (the run verdict is applied at the read); sub-project 1 corrected and gated`.

**Done when:** `docs/decisions.md` ends with D128, and the roadmap's sub-project 1 status block opens with `corrections landed 2026-09-<DD>` (the real date) and its `Gate over TRANSFORM (2026-09-<DD>)` paragraph quotes Task 9's `yaxis4` result and timings.

---

### Task 9: Sub-project gate, re-run

The foundations plan's Task 11, Steps 1–4, re-run at the corrected HEAD; the saved drivers in `.soul/foundations-gate/` take their paths as argv and are run with `uv run --no-sync python <driver> <args>` from the repo root, in a shell with `PLAYWRIGHT_BROWSERS_PATH=$HOME/.cache/ms-playwright` when `XDG_CACHE_HOME` is exported.

**Files:** none changed by this task (Task 8 records the numbers).

- [ ] **Step 1: The suite, read from the logs.** The four gates of the Conventions. Expected: ruff/format clean outside `docs/design/authoring-tool/`; bare mypy clean; the not-playwright run's summary line shows `0 failed`, `0 error` (skips: the 3 Julia ones, plus the root-only skip of Task 6 if the session is root); the playwright run shows every browser test passing, no skips. Then `ls ~/.cache/dstf/scan/` before and after the suite: identical listings (the autouse redirect held).

- [ ] **Step 2: Predicates over the end state:**
  1. Under the report's plotly.js and the tool's, an axis is visible exactly while a series on it is visible, by select or legend, with a foreign restyle listener present and after a react (Task 2's file; Task 3's smoke test) — BOTH halves now.
  2. `backend.read_results` and `read_last_results` over a manifest recording `timed_out` return `success=False` (Task 1's tests).
  3. `Session.figure` after each of the three scored-list transitions equals a fresh Session's (Task 4); a stale cached open scores an added model as a cold open does (Task 5); an open over an unwritable cache dir opens (Task 6); `done == total` on every open, with or without a spec-only test (Task 7).
  4. `grep -rln "nicegui" src/dstf/authoring/` lists only `app.py`, `forms.py` and `builtin/*`.

- [ ] **Step 3: The report over TRANSFORM (the orchestrator, over a COPY).** As the foundations plan's Task 11 Step 3: copy `testing_output/TRANSFORM/Dymola/linux` to a scratch dir, `uv run --no-sync dstf --config /mnt/d/Modelica/TRANSFORM-UnitTests/ReferenceResults/testing.json compare --work-dir <copy> --report --filter "MSLFluid.InverseParameterization"`, then `report_gate.py <page> <outdir>` and `report_both.py <page> <outdir>`: `nrmse` → `yaxis4` visible with title `NRMSE [–]`, `signed` → `yaxis3` visible and `yaxis4` hidden; body font ends in `sans-serif`; no page errors. Thumbnail and READ the screenshot: two axis titles at the right, no overlap. Additionally, the verdict parity over the copy, read-only:

```
uv run --no-sync python - <<'EOF'
from pathlib import Path
from dstf.config import Config
from dstf.backends import select_backend
from dstf.discovery.test_registry import discover_tests
from dstf.storage.batch_manifest import BatchManifest
cfg = Config(config_file=Path("/mnt/d/Modelica/TRANSFORM-UnitTests/ReferenceResults/testing.json"),
             work_dir=Path("<copy>"))
backend = select_backend(cfg, persistent=False); backend.quiet = True
tests = discover_tests(cfg)
m = BatchManifest.load(cfg.work_dir / "batch_manifest.json")
bad = {e["model_id"] for e in m.manifest.values() if e.get("last_run_status") in ("failed", "timed_out")}
merged = backend.read_results([m], tests)
print("recorded failed/timed_out:", len(bad), "of which read as success:",
      sorted(k for k in bad if k in merged and merged[k].success))
EOF
```

Expected: `of which read as success: []`. Record the first number; if it is 0 the predicate is vacuous over this copy and the tests carry it — say so.

- [ ] **Step 4: The tool over TRANSFORM (the orchestrator, over the COPY from Step 3).** `tool_gate.py` hardcodes `WORK` (line 9) to the real work dir: point it at Step 3's copy first (the driver is untracked scratch), then `tool_gate.py <outdir> cold <scratch-xdg>` and `tool_gate.py <outdir> warm <scratch-xdg>` (it launches `dstf author` over `WORK` with `--no-browser`, times the first 200 and the clicks, clicks the legend entry "NRMSE (per-point)" and probes both axes, screenshots the centre). Then, by hand in the same page (Playwright or the browser's console): with the NRMSE trace shown by the legend, open Edit criteria for the selected leaf and change a tolerance field (a staged edit — `update_figure` goes through `Plotly.react`, nothing is saved; Discard it after) and re-read `document.querySelector('.js-plotly-plot')._fullLayout.yaxis4.visible` → true: this is the only exercise of the react follow in the tool, NiceGUI's plotly.js 3.1.1. Expected changes from the previous gate: the legend probe now reports `yaxis4` True (was `[False, False]`); the open and click timings within the previous numbers (cold ~48 s, warm ~24 s, first click ~2.5 s, later ~1.2 s — not targets of this plan). `rescan_check.py` and `session_timing.py` (both hardcode `WORK` too — point them at the copy): rescan `changed False`, the cache file not rewritten; `Session.figure` under 100 ms first, under 5 ms after. Before/after: the spec's md5, the reference-dir listing and the copy's listing unchanged; `TRANSFORM-UnitTests` tree clean. Thumbnail and READ the centre screenshot. Never click `Accept N` / `Rerun N`.

- [ ] **Step 5: Record.** The numbers go into Task 8's roadmap block (then Task 8 commits) and the handoff. The three gate-driver outputs and the two thumbnails stay in the session scratchpad.

**Done when:** every predicate in Step 2 holds by a named test that ran green in Step 1's logs, Step 3's parity print is an empty list, and Step 4's legend probe AND its post-edit react probe both report `yaxis4` visible in the tool page over the TRANSFORM copy.

---

## Self-review (run before handing the plan to review)

- **Coverage of the held items.** Task 3 + `axes.js` react (Task 2, Task 3); stale base (Task 4); rescan re-read (Task 5); `store_scan` guard + tmp name (Task 6); unannounced tests + partial read (Task 7); the run-verdict gap (Task 1); the folds a review asked for — the part-order test that cannot fail and the one-axis docstring (Task 2 Steps 6–7), the `figure` KeyError and the dropped reads (Task 4), probes c/d/e landed (Task 5). Not covered, by decision: the font token in the tool's body, the first-click time, the warn-dark contrast, the old-data verdict flip (header).
- **Placeholders.** None: every test and every change is written out. `<DD>` in Task 8 and `<copy>` / `<outdir>` / `<scratch-xdg>` in Task 9 are the build session's paths and date, filled at run time.
- **Type consistency.** `_store(model_id, result)` inside `read_results` (Task 1); `gd._dstfFollow` / `isWired` / `dstfWireSecondaryAxes` (Tasks 2, 3); `_ModelReads.scored` / `_scored_key` / `_base_kinds` / `_two_var_project` (Task 4); `_rebuild_tests(models, reread=...)` / `_reread_results(models)` / `_add_bar` (Task 5; `_rebuild_tests`'s `_reads.pop` on `gone` is written in Task 4 and kept in Task 5's listing); `store_scan` returning `Path | None` (Task 6); `score_and_announce` / `announced` / `failures` (Task 7). `TestResult` is imported in `session.py` already (`from ..backends.types import DatasetType, TestResult`); `threading` is imported in `test_session.py` already; Task 6's unit test file needs `os`, `threading`, `logging` added (Step 1 says so).
- **Fresh-context review of this text** (2026-09-16, before any build): ACCEPT-WITH-FIXES, 0 HIGH / 4 MED / 6 LOW, all ten folded — the `open` / `_finish_job` behaviour on Task 7's raise specified; the react-in-the-tool claim downgraded to "read, untested" with a hand probe in Task 9; Task 9's tool gate moved onto the copy; Task 7's M6 given its assertion (Step 3b); the purge wording, the M3 count, the page-error listener, the Task 8 predicate, `_scored_key`'s dead branch and Task 4's M3 corrected.
- **Red states, EXECUTED** (2026-09-16, at 91abfc2, every plan test copied verbatim into a scratchpad module and run with `-o pythonpath=tests -p conftest`): Tasks 1, 4, 5 (the added-model test), 6 and 7 fail at exactly the assertion each step names — 14 of 14 — and Task 5's three guards pass, as stated (a fourth guard, for the re-read's own-list filter, was added at the build's review — M5); Task 2's two browser tests fail as named under the report's plotly.js (`count` is 1; after the react the trace is visible and `yaxis4` is not, so `uirevision` DOES keep the legend state and the contingency in Step 3 is not expected to trigger). Task 3's red state was executed when the patch was first tried. The green side — that the implementations as written make them pass and the named mutations fail — is the build session's to show and the reviews' to check.
