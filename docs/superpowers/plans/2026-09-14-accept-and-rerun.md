# Accept and Rerun Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the tool's two remaining v1 capabilities. **Accept** turns the selected tests' current results into baselines through the shared accept function, as a `StoreWrite`. **Rerun** runs the selection through the configured backend on a worker thread, as a `Job`, with the job panel showing per-test progress from the `status.json` the pipeline already writes. Underneath them: the run pipeline extracted from `cmd_run` as a library callable (configuration and tests in, results out, exceptions instead of exits, no printing, no browser) with `cmd_run` as its CLI wrapper, and the `quiet` flag that stops a backend narrating a run to the console of a UI process.

**Architecture:** Sub-project 5 of `docs/superpowers/specs/2026-09-08-two-tier-architecture-design.md` (§4.6 as amended in sub-projects 3 and 4, §4.7 "Accept", "Rerun", "Failure handling", §5 step 5, §6 "End to end" and "Named as unrun", §7.4, §8 "A progress observer"). Sub-project 3 shipped the Session, the outcome kinds and the shared accept function (`storage/accept.accept_run_results`, already the function both CLI `--accept` paths call). Sub-project 4 shipped the shell and the two editing capabilities. This plan adds `src/dstf/pipeline.py` (the run callable, beside `config.py` and `plugins.py` — it orchestrates a backend, a store and a work dir and belongs to no one layer), `backends.select_backend` (the persistent-or-batch choice `cmd_run` made inline, raising where the CLI exited), `Backend.quiet`, two builtin capabilities, and the job panel's poll. The Session applies outcomes; Accept and Rerun apply nothing.

**Tech Stack:** Python 3.10+, NiceGUI 3.16 (the `[author]` extra), pytest-asyncio for `user_simulation`, uv. No new dependency (pytest-timeout is not one; waits are bounded by `Event.wait(60)` with the return value asserted).

**Revision:** this is the second text. A fresh-context review verified the first against the code and the fixtures by execution and found twelve task-breakers: the wipe helper takes the backend's `ref_id_map`, not the store's index; `--merge` needs the candidate list the signature did not carry; the no-baseline copy writes no `reference_manifest.json`; the fixture's C is `pending`, so accept refuses it with a different reason than the test asserted; B's baseline id is `0004`, not `0002`; `should_see`'s default budget is 0.3 s, shorter than the 1 s poll; four existing tests assert `no job running` after a job ends; an unopened project classifies SIM_FAIL, not NO_REF; ConstantCsv's `range` leaf is baseline-free and PASSES without a baseline; a `replace` on a missing `comparison` member is a PatchError; the review loop's `config` is optional and its tests use store fakes; opening the browser before the first `render_live` opens a missing file. All folded in, with the smaller findings (#13–#27 of that review).

**Spike results this plan rests on** (run 2026-09-14, scratch script over a temporary copy of `examples/python/PythonTestingLib` with its `Python/` baselines removed, `work_dir` passed explicitly): `Backend.run_tests` + `read_last_results` for the batch Python backend work from a non-main thread; one test (`ConstantCsv`, no scipy) runs in 0.48 s the first time and 0.23 s the second; `status.json` was readable five times during that half-second run (atomic replace, never a partial file); a **second `run_tests` on the same backend instance** works and reads back clean; `store.accept_results` on that copy wrote `Python/linux/ref_0001.json` with LF. Every console line the run prints (`Running 1 tests…`, `Backend: …`, `Live progress: …`, the progress bar, `Simulations complete…`, `Reading 1 result files…`, `Read phase: …`) was visible — that is what `quiet` removes. The inventory of `cmd_run` this plan is written against: `src/dstf/cli/run_cmds.py:154-339`; the only `sys.exit` on its path is `_get_backend`'s persistent preflight (`:138`); `_wipe_stale_state_for_scope` (`:33-85`) wipes in-scope sim dirs and report dirs only; `render_live` + `open_in_browser` (`:249-250`); `backend.run_tests` → `enrich_ref_ids` → cross-backend chains → `read_results` (prints) → `--accept` / `-i` / report; `--merge` widens the read scope to every model in the returned manifests; `--rerun` mutates `args.merge` (`:193`); `status.json` has exactly one writer (`ProgressReporter._write_json`); `Session._reload_results` reads back through `backend.read_last_results`, so the run callable's contract with the Session is what it leaves on disk, not what it returns.

---

## Conventions for every task

- Run commands from the repo root with `uv run --no-sync ...` (agents share the venv; nothing in this plan changes a dependency). Gates before every commit: `uv run --no-sync ruff check`, `uv run --no-sync ruff format --check` (fix with `ruff format <files>`), `uv run --no-sync mypy` **bare, no path**, `uv run --no-sync pytest -q -m "not playwright"`, and the tests each task names. The shell tests run (`.venv` carries the `[author]` extra); they `importorskip("nicegui")` elsewhere.
- **Commit with explicit paths — `git commit -F <msgfile> -- <paths>` — never a bare `git commit`.** New files need `git add <file>` first. Messages end with the attribution trailer the session's instructions specify. Nothing is pushed.
- **Never run `dstf run` / `dstf compare` / `dstf author` against `testing_output/` or any real work directory** from a task or a review; Task 11's real-library gate is the orchestrator's. Tests use `tmp_path`. A test that needs a runnable library copies `examples/python/PythonTestingLib` into `tmp_path` with `shutil.copytree` and passes `work_dir=` explicitly (the default work dir is derived from the CWD).
- Done-checks are predicates over the end state, never counts.
- `docs/ideas.md` and `.soul/` are NOT touched by this plan. `docs/decisions.md` gains no entry.
- **Every file under `src/dstf/authoring/` other than `app.py`, `forms.py` and `builtin/*` still imports no UI framework**; `builtin/*` import `nicegui` only inside functions (`import dstf.authoring` runs `register_builtins()`).
- **`src/dstf/pipeline.py` imports nothing from `dstf.cli`** — the CLI imports it, never the reverse.
- In a `user_simulation` test, after any click that refreshes a region, wait on something the refresh RENDERS (`await user.should_see(...)`) before asserting, and assert the Session's state too, never only the page's text.
- A scripted edit that replaces a function bounds on that function's own end; the test count is checked before the commit.
- No CLI behaviour changes for a user who never opens the tool (§7.4): `tests/test_cli.py`'s `cmd_run` tests pass unchanged except where a task names an edit.
- Calls made here, not in the spec (Task 10 records them in a §4.6 "Amended in sub-project 5" note):
  1. **`StoreWrite.run` returns `str | None`** — one line saying what the write did (Accept: stored N, skipped M with reasons). The Session appends it to `Session.notices` and emits `job` with `state="written"`; the job panel shows it. The spec's `Callable[[ReferenceStore], None]` had no way for a partial accept to say which tests it refused and why, and raising after some baselines were written would mislead ("Session unchanged" would be false).
  2. **`RunningJob.started_wall`** (epoch seconds at start): the job panel ignores a `status.json` whose `start_wall` is older, so the previous run's snapshot is never shown as this job's progress — the same idea as `dashboard_render`'s `written_at < start_wall` sidecar guard.
  3. **`Backend.quiet`** (instance attribute, default `False`): every run-narration print in `backends/` is gated on it. `Session.open` sets it on the project's backend; Rerun sets it on the backend it builds. The CLI's backend stays loud.
  4. **`backends.select_backend(config, *, persistent)`** raises `BackendUnavailableError` where `cli.run_cmds._get_backend` printed and exited; `_get_backend` stays as the CLI's wrapper (tests monkeypatch it by name).
  5. **Rerun builds its own backend per job** via `select_backend(..., persistent=True)`, exactly the CLI's default, rather than running through the batch instance `Session.open` holds for reading. The Session's instance is `get_backend`'s batch class; the tool's rerun should use the persistent workers a CLI run would.
  6. **The run callable takes `store` and `backend`** rather than building them: the CLI needs the backend before the run (`--rerun` reads the last results through it), and the Session holds both. It also takes `candidates` (the discovered tests `--merge` widens the read scope over); the CLI passes `all_tests`, the tool passes nothing and the scope is the run.
  7. **`progress.read_status(work_dir)`** is the one reader of `status.json` (public; `dashboard_render` and the shell use it).

## File structure

| Path | Responsibility after this plan |
|---|---|
| `src/dstf/pipeline.py` (new) | `RunOutcome`, `run_pipeline(config, tests, *, backend, store, merge=False, candidates=None)`, `wipe_stale_state_for_scope` (moved from `cli/run_cmds.py`), `write_id_mapping` (moved from `cli/common.py`), `run_cross_backend_chains` (moved; returns lines). |
| `src/dstf/backends/__init__.py` | `select_backend`, `BackendUnavailableError`. |
| `src/dstf/backends/base.py`, `persistent.py`, `dymola/backend.py` | `Backend.quiet`; `_print_run_header` / `_print_progress` / `_print_run_summary` and the read-phase prints gated. |
| `src/dstf/backends/progress.py` | `read_status(work_dir) -> dict \| None`. |
| `src/dstf/reporting/dashboard_render.py` | uses `read_status`; `_read_status` deleted. |
| `src/dstf/cli/run_cmds.py` | `cmd_run` = selection + `run_pipeline` + accept / review / report; `_get_backend` wraps `select_backend`; `cmd_compare` imports the moved helpers. |
| `src/dstf/cli/common.py`, `cli/diag_cmds.py`, `cli/review.py` | `write_id_mapping` imported from `pipeline`; the review loop's `[a]` goes through `accept_run_results`. |
| `src/dstf/authoring/capabilities.py` | `StoreWrite.run: Callable[[ReferenceStore], str \| None]`. |
| `src/dstf/authoring/session.py` | `notices`, the `written` event, `RunningJob.started_wall`, `open` sets `quiet`. |
| `src/dstf/authoring/builtin/accept.py`, `rerun.py` (new), `builtin/__init__.py` | The two capabilities, registered after Edit tree. |
| `src/dstf/authoring/app.py` | `_jobs` shows per-test rows from `status.json` while a job runs, the last job's outcome and error, the notices, the artifacts; a 1 s poll timer while a job runs. |
| `tests/test_pipeline.py`, `tests/test_accept_and_rerun.py` (new) | The callable; the §6 end-to-end scenario. |
| `tests/test_cli.py`, `tests/test_stale_state_wipe.py`, `tests/test_session.py`, `tests/test_shell.py`, `tests/test_builtin_capabilities.py`, `tests/test_capability_conformance.py`, `tests/test_review*.py` (whichever covers `_interactive_review`), `tests/test_openmodelica_persistent.py`, `tests/test_capability_gating.py` | Imports follow the moves; new assertions per task. |
| `docs/usage.md`, `docs/extensibility.md`, `docs/architecture.md`, `docs/qa/reporter_checklist.md`, `CLAUDE.md`, the spec §4.6 note | Updated (Task 10). |

---

### Task 1: `Backend.quiet`

**Files:**
- Modify: `src/dstf/backends/base.py` (`_print_run_header:82`, `_print_progress:129`, `_print_run_summary:473`, the call sites `:511,538,561,569,642`, the read-phase prints `:621,650-653`, `Backend.__init__`), `src/dstf/backends/persistent.py` (`:549,562,584,610,635,639,716,775-805` — every `print(` that narrates the run), `src/dstf/backends/dymola/backend.py` (`:141,234,449,493`)
- Test: `tests/test_backend_quiet.py` (new)

- [ ] **Step 1: The failing test** — first the helper every later task reuses, in `tests/conftest.py` (per its documented pattern, so no test module imports another): `python_example_copy(tmp_path, *, baselines=True) -> tuple[Config, list[TestModel]]` — `shutil.copytree(Path("examples/python/PythonTestingLib"), tmp_path / "lib")`, `Resources/ReferenceResults/Python/` removed when `baselines=False`, `Config(config_file=<copy>/Resources/ReferenceResults/testing.json, work_dir=tmp_path / "work")`, `discover_tests(config)`. Then `tests/test_backend_quiet.py`: keep only `ConstantCsv`; `backend = get_backend(config)`; with `backend.quiet = True`, `backend.run_tests(tests)` then `backend.read_last_results(tests)` leave `capsys.readouterr()` with empty `out` AND empty `err`, and the result is `success` with one variable. A second test: with the default (`quiet` False) the same run's `err` contains `"Running 1 tests"` and `out` contains `"Reading 1 result files"` — so the flag is what silenced it, not a moved print.

- [ ] **Step 2: The flag** — `Backend.__init__` sets `self.quiet: bool = False` (class attribute `quiet: bool = False` as the declaration, so a fake that never calls `__init__` still reads it). The two module-level helpers (`_print_run_header`, `_print_progress`) gain a keyword `quiet: bool = False` and return at once when it is set; the method `_print_run_summary` reads `self.quiet`; every call site in `base.py`, `persistent.py` and `dymola/backend.py` passes `quiet=self.quiet`. `reporting/report_suite.py:659-684` also calls `_print_progress` — that is the CLI's report phase, not a backend, and stays loud (no argument). The read-phase `print`s in `read_results` and the persistent pool's worker-lifecycle `print`s (`starting_workers_message`, the startup/restart/shutdown lines) are wrapped `if not self.quiet:`. Logger calls are not prints and stay. End state: **every `print(` in `backends/base.py`, `backends/persistent.py` and `backends/dymola/backend.py` that remains is either inside one of the narration helpers (which return on `quiet`) or under an `if not self.quiet` guard** — the one known exception is `dymola/backend.py:665`, a `print(` inside a Modelica script string literal, which is not Python; list any other in the commit message with the reason.

- [ ] **Step 3: Run** — `uv run --no-sync pytest tests/test_backend_quiet.py tests/test_openmodelica_persistent.py tests/test_dymola*.py tests/test_run_prologue.py tests/test_progress_reporter.py -q`. Gates.

- [ ] **Step 4: Commit** (paths: the three backend files, `tests/conftest.py`, `tests/test_backend_quiet.py`). Message: `feat: Backend.quiet — a run narrates the console only when asked`

---

### Task 2: `select_backend`

**Files:**
- Modify: `src/dstf/backends/__init__.py`, `src/dstf/cli/run_cmds.py:100-139`
- Test: `tests/test_capability_gating.py` (the `_get_backend` tests there), `tests/test_openmodelica_persistent.py:762-816`

- [ ] **Step 1: The failing tests** — in `tests/test_capability_gating.py`, beside the existing `_get_backend` tests: `select_backend(config, persistent=False)` returns the batch class for a batch-only backend; `select_backend(config, persistent=True)` returns the batch class for a backend without `PERSISTENT_WORKERS` (the Python backend); for a fake backend class declaring the capability whose `preflight` raises `RuntimeError("no omc")`, `select_backend(..., persistent=True)` raises `BackendUnavailableError` whose message contains `"no omc"` and `"--batch"`. In `tests/test_openmodelica_persistent.py`, the test asserting `SystemExit` from `cli_mod._get_backend` stays as it is — the CLI still exits 1 with the same stderr text.

- [ ] **Step 2: The function** — `backends/__init__.py`: `class BackendUnavailableError(RuntimeError)` and `select_backend(config, *, persistent: bool) -> Backend`, the body of today's `_get_backend` with the `print` + `sys.exit(1)` replaced by `raise BackendUnavailableError(f"Persistent-worker {backend} unavailable.\n\n{exc}\n\nOr re-run with \`--batch\` to use the per-test subprocess backend instead.") from exc`. `config.backend` is read only on that error path — the gating tests pass a `SimpleNamespace()` config (`tests/test_capability_gating.py:86,101`). `cli.run_cmds._get_backend(config, persistent)` becomes: try `select_backend`; on `BackendUnavailableError` print `f"{exc} Aborting."`-shaped text to stderr matching what the OM test asserts today, `sys.exit(1)`. Its docstring keeps the backend-agnostic note. Also translate, NARROWLY, the bare `ValueError` `get_backend_class` raises for an unregistered backend and the `RuntimeError` a backend's constructor raises when its executable is missing (`python/backend.py:64`, `julia/backend.py:68`) into `BackendUnavailableError` — `try` around exactly `get_backend_class(config)` and around each `cls(config)` call, nothing wider, so a genuine bug in a constructor still tracebacks. The CLI wrapper then exits 1 with the message instead of a traceback; this is a small unasked behaviour change, called out in the commit message.

- [ ] **Step 3: Run** — the two test files. Gates.

- [ ] **Step 4: Commit** (paths: `src/dstf/backends/__init__.py src/dstf/cli/run_cmds.py tests/test_capability_gating.py`). Message: `refactor: select_backend raises where the CLI exited; _get_backend is its wrapper`

---

### Task 3: The run callable (own commit, §4.7 "Rerun")

**Files:**
- Create: `src/dstf/pipeline.py`
- Modify: `src/dstf/cli/run_cmds.py` (`cmd_run`, `cmd_compare`, delete `_wipe_stale_state_for_scope` and `_run_cross_backend_chains`), `src/dstf/cli/common.py` (delete `_write_id_mapping`), `src/dstf/cli/diag_cmds.py:11,112`
- Test: `tests/test_pipeline.py` (new), `tests/test_stale_state_wipe.py:20` (import), `tests/test_cli.py:25,1347` (import)

- [ ] **Step 1: The failing tests** — `tests/test_pipeline.py`, over Task 1's `python_example_copy` conftest helper. The tests, with baselines (three `ref_000N.json` exist, so `write_id_mapping` has something to write):
  1. `run_pipeline(config, [ConstantCsv], backend=backend, store=store)` returns a `RunOutcome` whose `results` has exactly `ConstantCsv` with `success`, whose `scope_tests` is `[ConstantCsv]`, and whose `notices` is `[]`; afterwards `work_dir` holds `batch_manifest.json`, `status.json`, `dashboard.html`, `reference_manifest.json` (mapping the three ref ids to the three model ids) and `test_0001/result.json`; `read_status(work_dir)["counts"]["passed"] == 1`.
  2. A second call, same backend and store, `[SimpleRamp]` (scipy; ~4.5 s — acceptable once) with `merge=True, candidates=all_three`: `scope_tests` names both run models in `candidates` order and `results` holds both, `ConstantCsv`'s from the first run's files (its `test_0001` dir untouched: record its `result.json` mtime before and assert equal after). Without `candidates`, `merge=True` widens over `tests` only — assert `scope_tests == [SimpleRamp]` for that call.
  3. With `backend.quiet = True`, call 1 leaves `capsys` empty (the callable itself prints nothing; the backend is quiet).
  4. A `run_tests` that raises `RuntimeError("all workers dead")` (a fake backend with `run_tests` raising — the `_FakeBackend` shape from `tests/test_cli.py:365-397`, rebuilt locally in this module with the raising method) propagates unchanged out of `run_pipeline`; nothing catches it, no `SystemExit` is involved (`pytest.raises(RuntimeError)`).
  5. `run_pipeline` with an empty `tests` list returns `RunOutcome(manifests=[], results={}, scope_tests=[], notices=[])` and writes nothing (`sorted(work_dir.rglob("*"))` unchanged) — the "No tests matched" decision stays the CLI's.
  6. `tests/test_stale_state_wipe.py` imports `wipe_stale_state_for_scope` from `dstf.pipeline` (`:20`; its module docstring at `:1` names the old name too — update it); `tests/test_cli.py` imports `write_id_mapping` from `dstf.pipeline` (`:25`) and calls it by the new name at `:1347`; both pass unchanged otherwise. `src/dstf/reporting/dashboard_render.py:76` names `_wipe_stale_state_for_scope` in a comment — update it.

- [ ] **Step 2: The module** — `src/dstf/pipeline.py`:

```python
@dataclass(frozen=True)
class RunOutcome:
    manifests: list[BatchManifest]
    results: dict[str, TestResult]      # every model in scope
    scope_tests: list[TestModel]        # `tests`, or every manifest model when merge
    notices: list[str]                  # the cross-backend chain's lines; the CLI prints them

def run_pipeline(config, tests, *, backend, store, merge=False, candidates=None) -> RunOutcome:
```
  Body, in `cmd_run`'s order (`run_cmds.py:161-283`) minus the CLI: return the empty outcome for no tests; `write_id_mapping(store, config)`; pre-populate `backend.ref_id_map[t.model_id] = f"ref_{store.index.get_id(t.model_id)}"` for every scoped test that has an id (exactly `run_cmds.py:224-227`); `config.work_dir.mkdir(parents=True, exist_ok=True)`; `wipe_stale_state_for_scope(config, tests, backend.ref_id_map, wipe_sim_dirs=True)` — the third argument is the backend's map, as `run_cmds.py:235-240` passes it (`RefIndex` has no `.get`); `render_live(config.work_dir, rerun_prefix=build_rerun_prefix(config))` (a file, not a browser — the initial paint before the first status write; idempotent, so the CLI may also have called it a moment earlier); `manifests = backend.run_tests(tests)` (exceptions propagate); `enrich_ref_ids`; `notices = run_cross_backend_chains(tests, backend, config, store)` (the function moves here and RETURNS its lines instead of printing them — every `print` in it becomes an append); the `merge` scope widening over `candidates if candidates is not None else tests` (today's `run_cmds.py:275-281` over `all_tests`); `results = backend.read_results(manifests, scope_tests)`. No `print`, no `sys.exit`, no `open_in_browser`, no `input`. Module docstring: what it is (the library form of `dstf run`), what stays on disk, and that the Session reads the run back through `read_last_results`.

  `cmd_run` after: config, discover, filter, orphans notice, store, `write_id_mapping(store, config)` (kept here too — `run_cmds.py:164` writes it before the backend is even chosen, so the `--rerun` "Nothing to rerun" and the "No tests matched" paths keep refreshing `reference_manifest.json` as today), `_get_backend`, the `--rerun` selection (unchanged, still mutating `args.merge` — a `Namespace` is per invocation), `if not tests: print + return 1`, then `config.work_dir.mkdir(...)`, `render_live(...)`, `open_in_browser(config.work_dir / "dashboard.html")` in that order — today's `:246-250`, so the tab never opens a file that does not exist yet — then
  ```python
  try:
      outcome = run_pipeline(config, tests, backend=backend, store=store, merge=args.merge, candidates=all_tests)
  except RuntimeError as exc:
      print(f"\nRun aborted: {exc}", file=sys.stderr); return 1
  for line in outcome.notices: print(line)
  results, scope_tests = outcome.results, outcome.scope_tests
  ```
  and the `--accept` / `-i` / report tail exactly as today. The `except RuntimeError` now also covers the wipe, the chains and `read_results`, where today it covered `run_tests` alone (`:252-259`) — a `RuntimeError` from a result reader now prints "Run aborted" and exits 1 instead of a traceback; say so in the commit message. Keep the existing monkeypatch seam: `cmd_run` imports `open_in_browser` lazily from `dstf.reporting.report_suite` as today (`tests/test_cli.py:401-417` patches that name).

  `cmd_compare` imports `wipe_stale_state_for_scope` and `write_id_mapping` from `pipeline`; `diag_cmds` likewise.

- [ ] **Step 3: Run** — `uv run --no-sync pytest tests/test_pipeline.py tests/test_cli.py tests/test_stale_state_wipe.py tests/test_reporters.py -q`. Gates. The `tests/test_cli.py` run tests pass with no edit beyond the import — that is §7.4's predicate for this task.

- [ ] **Step 4: Commit** (paths: `src/dstf/pipeline.py src/dstf/cli/run_cmds.py src/dstf/cli/common.py src/dstf/cli/diag_cmds.py src/dstf/reporting/dashboard_render.py tests/test_pipeline.py tests/test_stale_state_wipe.py tests/test_cli.py`). Message: `refactor: run_pipeline — the run as a library callable; cmd_run is its CLI wrapper`

---

### Task 4: The Session's side — notices, `started_wall`, `quiet`, `read_status`

**Files:**
- Modify: `src/dstf/authoring/capabilities.py` (`StoreWrite`), `src/dstf/authoring/session.py` (`RunningJob`, `Session.__init__`, `open`, `close`, `apply`), `src/dstf/backends/progress.py`, `src/dstf/reporting/dashboard_render.py:39-46`
- Test: `tests/test_session.py`, `tests/test_progress_reporter.py`

- [ ] **Step 1: The failing tests** — in `tests/test_session.py` `TestOutcomes`: a `StoreWrite` whose `run` returns `"stored 1"` leaves `session.notices == ["stored 1"]` and a listener on `job` received `{"state": "written", "message": "stored 1"}`; one returning `None` appends nothing and emits no `written` event; `close()` empties `notices`. `TestLockAndCompletion`: `session.running.started_wall` is within 5 s of `time.time()` right after `apply(Job(...))`. `TestOpen`: a test that takes `capsys` and `tmp_path` (NOT the `session` fixture — that fixture opens before capture starts, so the assertion would pass against nothing) builds `_project(tmp_path)`, calls `Session().open(cfg, work_dir=work)` in its own body, and asserts `session.project.backend.quiet is True` and `capsys.readouterr().out == ""` (the fixture's work dir has a manifest, so `read_last_results` prints `Reading 5 result files` today — verified). In `tests/test_progress_reporter.py`: `read_status(work_dir)` returns the dict for a written file, `None` when absent, `None` for a file holding `"{"` (undecodable — the writer's atomic replace means this only happens for a foreign file, but the reader must not raise into a UI timer).

- [ ] **Step 2: The changes** — `StoreWrite.run: Callable[[ReferenceStore], str | None]` with the docstring saying the return is the one line the job panel shows. `Session.__init__`: `self.notices: list[str] = []`; `close` resets it. `apply(StoreWrite)`: `message = outcome.run(store)`; `self._reload_results()`; then `if message: self.notices.append(message); self._emit("job", state="written", message=message)` — the reload first, so a listener that reads verdicts on `written` sees the accepted baseline. `RunningJob.started_wall: float = field(default_factory=time.time)`. `open`: `backend_obj.quiet = True` right after `get_backend`. `progress.read_status(work_dir) -> dict | None` = today's `dashboard_render._read_status` verbatim (+ `OSError` → `None`); `dashboard_render` imports it and its private copy is deleted. `SessionView.notices` property (read-only tuple), beside `artifacts`.

- [ ] **Step 3: Run** — `uv run --no-sync pytest tests/test_session.py tests/test_progress_reporter.py tests/test_dashboard_render.py tests/test_capability_conformance.py -q` (the conformance test's `StoreWrite` dummy returns `None` and still passes). Gates.

- [ ] **Step 4: Commit** (paths: the four source files and the two test files). Message: `feat: a StoreWrite reports one line; a job knows when it started; the Session's backend is quiet`

---

### Task 5: Accept

**Files:**
- Create: `src/dstf/authoring/builtin/accept.py`
- Modify: `src/dstf/authoring/builtin/__init__.py`
- Test: `tests/test_builtin_capabilities.py`

- [ ] **Step 1: The failing tests** — append to `tests/test_builtin_capabilities.py` (its `shell` fixture builds the `_project` of `tests/test_session.py`: A FAIL with a result and baseline `ref_0001`, B NO_REF with a result and no baseline, C no result and `pending` in the batch manifest, D PASS with `ref_0002`, G PASS with `ref_0003` — so B's baseline, when stored, is allocated `0004`):

```python
async def test_accept_is_greyed_without_a_result_and_stores_the_rest(shell):
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.select(tests={C})
        await user.should_see("no successful result in the selection")
        shell.session.select(tests={A, B, C})
        await user.should_see("Accept")
        user.find("Accept").click()                  # the tab (lowest id); the button reads "Accept 2"
        await user.should_see("Accept 2")            # names what it will store: A and B; C is listed as refused
        await user.should_see("re-run before accepting")  # the panel row for C, from unfinished_run_models
        user.find("Accept 2").click()
        await user.should_see("Accept: stored 2")    # the notice, in the footer
        notice = shell.session.notices[-1]
        assert "FakeLib.C" in notice and "re-run before accepting" in notice
        assert classify(shell.session.comparisons[B]) == PASS   # NO_REF -> PASS: the reload saw the new baseline
        assert classify(shell.session.comparisons[A]) == PASS   # re-baselined
        rid = shell.session.project.store.index.get_id(B)
        assert rid == "0004"
        raw = (shell.session.project.config.reference_dir / f"ref_{rid}.json").read_bytes()
        assert b"\r" not in raw and json.loads(raw)["model_id"] == B


def test_accept_act_returns_a_store_write_over_the_selection(shell):
    # pure, no user_simulation: shell.session / shell.view from the module's own fixture
    shell.session.select(tests={A, B, C})
    out = Accept().act(shell.view)
    assert isinstance(out, StoreWrite) and out.models == {A, B, C}
    line = out.run(shell.session.project.store)
    assert line.startswith("Accept: stored 2") and "FakeLib.C" in line
```
  and a lock test: with a gated job on A running, selecting `{A}` greys Accept with `"rerun is running on FakeLib.A"` (assert on the tab's greyed text via `should_see`, then release the gate and `await user.should_see("no job running", retries=20)`).

- [ ] **Step 2: The capability** — `builtin/accept.py`: `name = "accept"`, `label = "Accept"`, `scope = Scope.TESTS`. `available`: the lock reason (`f"{s.running.label} is running on {model}"`) if any selected model is locked; `"no successful result in the selection"` unless some selected model has `s.project.results.get(m)` with `success`; else `True`. `panel`: one row per selected model in project order — `model_id · <status word from classify(s.comparisons[m])> · <what accept will do>` where the last is `will store`, `no simulation result`, `simulation failed`, or `re-run before accepting` for a model in `unfinished_run_models(project.config.work_dir)` (so the confirm list never promises what the accept function refuses) — and a button `Accept N` where N counts the `will store` rows, calling `ui_panel.submit()`. `act`: builds `tests = [t for t in project.tests if t.model_id in selection]`, `results = dict(project.results)`, `work_dir = project.config.work_dir`, and returns

  ```python
  StoreWrite(models=frozenset(selection), run=lambda store: _report(*accept_run_results(store, tests, results, work_dir), reference_dir=project.config.reference_dir))
  ```
  where `_report(stored, skipped, *, reference_dir)` returns `f"Accept: stored {stored} baseline(s) to {reference_dir}"` plus `"; skipped {model}: {reason}"` per skip. `ProjectView` already exposes `config`, `tests`, `results`. Register after Edit tree in `register_builtins`.

- [ ] **Step 3: Run** — the test file and `tests/test_capability_conformance.py`. Gates.

- [ ] **Step 4: Commit** (paths: `src/dstf/authoring/builtin/accept.py src/dstf/authoring/builtin/__init__.py tests/test_builtin_capabilities.py`). Message: `feat: Accept — the selection's results become baselines through the shared accept function`

---

### Task 6: Rerun

**Files:**
- Create: `src/dstf/authoring/builtin/rerun.py`
- Modify: `src/dstf/authoring/builtin/__init__.py`
- Test: `tests/test_builtin_capabilities.py`

- [ ] **Step 1: The failing tests** — a pure test: `Rerun().act(view)` with `{A, D}` selected returns a `Job` with `label == "rerun"`, `models == {A, D}`, `reloads_results`; with `dstf.authoring.builtin.rerun.run_pipeline` and `select_backend` monkeypatched to record their arguments, `job.run(JobContext(project=..., models={A, D}))` called `select_backend(config, persistent=True)`, set `quiet` on what it returned, and called `run_pipeline(config, [A's TestModel, D's TestModel] in project order, backend=<that>, store=project.store)`. A `user_simulation` test: select `{A}`, click the `Rerun` tab, the panel holds an element marked `rerun-backend` whose text is `runs through Python` (the header already reads `backend: Python`, so a `should_see("backend: Python")` would pass against the header — assert via `user.find(marker="rerun-backend")`) and a `Rerun 1` button; with a gated job running, the tab is greyed with `"rerun is running"`. The real run is Task 8's.

- [ ] **Step 2: The capability** — `builtin/rerun.py`: `name = "rerun"`, `label = "Rerun"`, `scope = Scope.TESTS`. `available`: `f"{s.running.label} is running"` while any job runs (one job at a time — `Session._start_job` refuses a second anyway; the tab says so before the click); else `True`. `panel`: a label `runs through {s.project.config.backend_name}` marked `rerun-backend`, the selected models in project order, a `Rerun N` button → `ui_panel.submit()`. `act` returns

  ```python
  Job(label="rerun", models=frozenset(selection), run=_run)
  def _run(ctx: JobContext) -> None:
      backend = select_backend(ctx.project.config, persistent=True); backend.quiet = True
      tests = [t for t in ctx.project.tests if t.model_id in ctx.models]
      run_pipeline(ctx.project.config, tests, backend=backend, store=ctx.project.store)
  ```
  The return is discarded: `_finish_job` reloads through `read_last_results`. A `BackendUnavailableError` or a `RuntimeError` from `run_tests` lands in `job.error` and the panel (Task 7). Register after Accept.

- [ ] **Step 3: Run**, gates.

- [ ] **Step 4: Commit** (paths: `src/dstf/authoring/builtin/rerun.py src/dstf/authoring/builtin/__init__.py tests/test_builtin_capabilities.py`). Message: `feat: Rerun — the selection runs through the configured backend as a Job`

---

### Task 7: The job panel on `status.json`

**Files:**
- Modify: `src/dstf/authoring/app.py` (`Shell.__init__`, `_on_job`, `root`, `_jobs`)
- Test: `tests/test_shell.py`

- [ ] **Step 1: The failing tests** — in `tests/test_shell.py`, beside the existing job-footer test (`:153-156`). **`User.should_see` retries 3 × 0.1 s by default — 0.3 s, shorter than the 1 s poll — so every wait on the poll passes `retries=20`** (the conformance test already uses `retries=50`). A gated job on `{A}` whose `run` first writes `<work_dir>/status.json` in the writer's shape (`total 1, counts {"running": 1}, tests [{"test_key": "test_0001", "model_id": A, "status": "running", "phase": "simulating", "elapsed": 0.5, ...}], start_wall = time.time()`) then waits on the gate; `await user.should_see("running: rerun")`, then `await user.should_see("simulating", retries=20)`; release the gate; `await user.should_see("finished: rerun", retries=20)`; the footer still reads `no job running` (the idle label stays — four existing tests assert it after a job ends: `tests/test_shell.py:156,326`, `tests/test_capability_conformance.py:291`, `tests/test_builtin_capabilities.py:109`) and no longer shows `simulating`. A second test: a `status.json` already on disk with `start_wall = time.time() - 3600` and a row `"status": "failed"` is NOT shown while a gated job runs (`should_not_see("failed")` after `should_see("running: rerun")`; the `_project` fixture's own `status.json` has NO `start_wall` key — the reader must tolerate that). A third: a job whose `run` raises `RuntimeError("no omc")` → `await user.should_see("failed: rerun", retries=20)` and `"no omc"`. A fourth: after a `StoreWrite` returning `"stored 1"`, the footer shows `stored 1`. A fifth: after a finished job on `{A}` where the reloaded result for A carries `error_message = "division by zero"` (write a failing `result.json` for A before releasing the gate — `{"success": false, "error": "division by zero"}`, the Python driver's shape, which `json_driver.read_result` maps to `error_message`), the footer's row for A shows `division by zero` (§4.7: "the backend's failure message from the results").

- [ ] **Step 2: The panel** — `Shell.__init__`: `self.last_job: RunningJob | None = None`. `_on_job`: on `finished` / `failed` set `self.last_job = event["job"]`; the refresh stays UNCONDITIONAL (`jobs`, `tests`, `tabs`) for every job event including `written` — the `results` event deliberately skips tabs, so this is what re-renders the Accept panel's rows and its `Accept N` after an accept. `root`: `ui.timer(1.0, self._poll, immediate=False)` where `_poll` refreshes `jobs` only while `self.session.running is not None`. `_jobs`:
  - always first: `running: {label}` when a job runs, else `no job running` (unchanged text).
  - running: `status = read_status(work_dir)`; if it exists and `(status.get("start_wall") or 0.0) >= job.started_wall - 1.0`: one row per `tests[]` entry whose `model_id` is in `job.models`: `model_id · status · phase · elapsed s` (`detail` when present).
  - not running, `last_job` set: a second line `finished: {label}` or `failed: {label} — {type(error).__name__}: {error}`; then one row per `last_job.models` in project order with the reloaded result's `error_message` when the result exists and is not `success` (from `session.project.results`).
  - then every `session.notices` line, then the artifact links as today.
  Marks: `.mark("job-row")` on the rows so a test can count them if it needs to. The `RunningJob` reference for a running job is `self.session.running`; the finished one is `self.last_job`. Nothing in `_jobs` may raise: a malformed status row (`.get` everywhere) renders what it has.

- [ ] **Step 3: Run** — `uv run --no-sync pytest tests/test_shell.py tests/test_capability_conformance.py -q`. Gates.

- [ ] **Step 4: Commit** (paths: `src/dstf/authoring/app.py tests/test_shell.py`). Message: `feat: the job panel — per-test progress from status.json, the last job's outcome, the write notices`

---

### Task 8: End to end (§6)

**Files:**
- Test: `tests/test_accept_and_rerun.py` (new)

- [ ] **Step 1: The test** — pure Python, no NiceGUI: `python_example_copy(tmp_path, baselines=False)` (no `Python/` partition: nothing has a baseline), `work_dir = tmp_path / "work"` (does not exist yet — no run has happened); `Session()` (no `post`: completion runs on the worker thread; the test waits on `running.done`); `open(config_path, work_dir=work_dir)`; assert three tests, every one `SIM_FAIL` with `error_message == "No simulation results found"` (no work dir → `read_last_results` is `{}` → `compare_one(result=None)`; verified — NOT `NO_REF`, which needs a result), `project.backend.quiet`, and `capsys` empty. Then, in order:
  1. `select(tests={ConstantCsv})`; `Rerun().act(view)` → `session.apply(job)`; `job = session.running`; **immediately** `session.stage([PatchOp("replace", "/comparison/variable_overrides/x/max_value", 2.0)])` for `ConstantCsv` raises `LockedError` naming `ConstantCsv` and `"rerun"` (the lock check precedes op parsing, so the op's validity does not matter there), while `PatchOp("add", "/comparison/tolerance", 0.5)` on `SimpleRamp` is accepted (pending count 1 — `add`, because no entry has a `comparison` member and `replace` on a missing member is a PatchError; `/simulation/*` is outside the stageable whitelist) — the lock is `running.models`, set before the thread starts, so this is deterministic; `session.discard()`.
  2. `assert job.done.wait(60)`; `job.error is None`; `session.running is None`; `project.results[ConstantCsv].success`; `classify(comparisons[ConstantCsv]) == PASS` with `comparisons[ConstantCsv].has_reference is False` (its only leaf is `range`, baseline-free — it scores without a baseline; verified); `read_status(work_dir)["counts"]["passed"] == 1`; the other two tests are still `SIM_FAIL` (`ConstantCsv` alone ran).
  3. `Accept().act(view)` → `session.apply(store_write)`; `session.notices[-1]` starts with `"Accept: stored 1"`; `classify(comparisons[ConstantCsv]) == PASS` and now `has_reference is True` — the reload saw the baseline; `config.reference_dir / "ref_0001.json"` exists (first id on an empty partition), `json.loads` gives `model_id == ConstantCsv`, and its bytes contain no `b"\r"`.
  4. Rerun `{ConstantCsv}` again (a second run on a fresh backend, same store): completes, `PASS` with `has_reference` stands, the ref file is byte-identical (the run did not touch the store).
  5. `capsys.readouterr()` is empty on both streams for the whole test — the tool's process never narrated.
  pytest-timeout is not a dependency: `job.done.wait(60)` is the bound and the `assert` on its return value the failure.

- [ ] **Step 2: Run** — `uv run --no-sync pytest tests/test_accept_and_rerun.py -q -s` once to see the time (expect < 5 s), then without `-s`. Gates.

- [ ] **Step 3: Commit** (path: the test). Message: `test: open, rerun one test, accept it — the reference file exists with LF, results refreshed, a locked model refuses an edit`

---

### Task 9: The review loop's `[a]` through the shared accept function

**Files:**
- Modify: `src/dstf/cli/review.py:140-160`
- Test: the module that covers `_interactive_review` (`grep -ln "_interactive_review" tests/` — `tests/test_review*.py` or `tests/test_cli.py`)

- [ ] **Step 1: The failing tests** — the two existing `[a]` tests in `tests/test_cli.py:672-706` drive `_interactive_review` with store FAKES (`_AcceptingStore`, `_PoisonStore` — only `store_reference`) and no config; both are rewritten over a real `ReferenceStore(Config(...))` on a `tmp_path` project with a COMPLETED `batch_manifest.json` (the `tests/test_accept.py:25` `_env` shape with `last_run_status: "passed"`), the poison case via a result holding `nan` (`_mk_result(values=[..., float("nan"), ...])`), asserting the same outcomes they assert today (accepted count; the poison skipped with its reason and the loop continuing). A new test: with the manifest marking the reviewed test `pending`, answering `a` prints `"re-run before accepting"` and stores nothing (`reference_dir` has no new file). Drive `input` with the pattern those tests use.

- [ ] **Step 2: The change** — `_interactive_review`'s `config` parameter becomes required (`cmd_run:306` passes it; it was `config=None` for the fakes). Replace the direct `store.store_reference(test, result)` (`:145`) and its surrounding `ValueError` handling with `stored, skipped = accept_run_results(store, [test], {test.model_id: result}, config.work_dir)`: `stored == 1` counts as accepted; a skip prints its reason and counts as skipped; `stored == 0 and not skipped` is the `simulate_only` case, which `accept_results` neither stores nor skips — print `"simulate-only test: no baseline stored"` and count it skipped. Today the loop stores a simulate-only test's baseline through `store_reference`; that stops, and the commit message says so (it is what `--accept` has always done).

- [ ] **Step 3: Run**, gates. **Step 4: Commit** (paths: `src/dstf/cli/review.py tests/test_cli.py`). Message: `fix: run -i accepts through the shared accept function, so it refuses what compare --accept refuses`

---

### Task 10: Documents

**Files:** `docs/usage.md` (the tool section gains Accept and Rerun: the confirm list, the notice line, the job panel's rows, "no cancel", the backend the rerun uses, that a rerun's dashboard shows the rerun subset as `--merge` does; the `Backend.quiet` note is internal and goes to extensibility), `docs/extensibility.md` (the capability contract: `StoreWrite.run` returns the one line; `RunningJob.started_wall`; `SessionView.notices`; the persistent-worker section: `select_backend` / `BackendUnavailableError`, `Backend.quiet` and the rule that a new print in a backend is gated on it; a "the run as a library" paragraph naming `pipeline.run_pipeline` and what it leaves on disk), `docs/architecture.md` (the layer ↔ code mapping gains a line for `pipeline.py` and one for `cli/run_cmds.py` as its wrapper — there is no `run_cmds` line today), `docs/qa/reporter_checklist.md` (the `## Authoring tool` section gains: select two tests, Accept, watch the notice and the verdicts flip; select one, Rerun, watch the row in the footer, the verdict after; a job's failure line), `CLAUDE.md` (the tree: `pipeline.py` line; `builtin/ (edit criteria + edit tree + accept + rerun)`; "Running the Tool": nothing new), the spec's §4.6 "Amended in sub-project 5" note (Conventions 1–7, briefly), README.md only if it lists the tool's capabilities.

- [ ] Gates; commit (paths listed). Message: `docs: accept and rerun — the run callable, the write notice, the job panel`

---

### Task 11: Sub-project gate

- [ ] **Step 1: Full gates** — `uv run --no-sync pytest -q` (browser tests included), `uv run --no-sync ruff check && uv run --no-sync ruff format --check && uv run --no-sync mypy`, `uv run --no-sync pytest --cov -q | tail -3`.
- [ ] **Step 2: End-state predicates**
  1. `grep -n "sys.exit\|print(\|open_in_browser\|input(" src/dstf/pipeline.py` is empty; `grep -n "from \.cli\|from dstf.cli\|import dstf.cli" src/dstf/pipeline.py` is empty.
  2. Every `print(` remaining in `src/dstf/backends/base.py`, `persistent.py`, `dymola/backend.py` is inside one of the narration helpers or under an `if not self.quiet` guard, except `dymola/backend.py:665` (Modelica inside a string literal); read them; list any other exception with its reason.
  3. `grep -rn "store_reference(" src/dstf/cli` is empty — every CLI accept goes through `accept_run_results`.
  4. `grep -rn "_read_status\|_wipe_stale_state_for_scope\|_write_id_mapping\|_run_cross_backend_chains" src tests` is empty — code AND prose (Task 3 names the two docstrings/comments); the moves left no alias.
  5. Module-level `nicegui` imports under `src/dstf/authoring` are exactly `app.py` and `forms.py`; `uv run --no-sync python -c "import dstf.authoring.builtin.accept, dstf.authoring.builtin.rerun"` succeeds in the no-extra venv from plan 4's gate (`/tmp/.../noauthor`, rebuild it if gone).
  6. Four builtins registered, each with a test that submits through the shell; the conformance test passes for every `Scope` and `Outcome` kind.
  7. `git diff --stat 75e91f7 -- tests/golden` empty.
  8. `tests/test_cli.py`'s `cmd_run` tests are unchanged except the import lines, the `write_id_mapping` call at `:1347`, and the `TestInteractiveReviewExitCode` class with its `_review_env` helper — Task 9 made `config` required, so every caller in that class passes a real one (`git diff cac3c70 -- tests/test_cli.py` shows nothing else).
- [ ] **Step 3: Real-library gate** (orchestrator only, from the orchestrator's session): (a) `uv run --no-sync dstf --config /mnt/d/Modelica/TRANSFORM-UnitTests/ReferenceResults/testing.json compare --work-dir testing_output/TRANSFORM/Dymola/linux --report` — 330 PASS + 1 SIM_FAIL unchanged, the report renders; (b) `dstf author --no-browser` on the same config and work dir, driven by a Chromium scratch script (scratchpad; not named after a stdlib module; `PYTEST_CURRENT_TEST` stripped from the env): 331 rows; select one PASS test; the Accept tab lists it with `will store` and the Rerun tab reads `backend: Dymola`; click NEITHER; the footer reads `no job running`; the console that ran `dstf author` shows no `Reading 331 result files` line (the quiet flag over the real consumer); terminate; the spec file's hash, the reference dir listing and the work dir listing unchanged before and after. Report the open time.
- [ ] **Step 4: Plan-versus-spec check** — a fresh-context agent reads §4.6 (as amended, all three notes), §4.7 "Accept" / "Rerun" / "Failure handling", §5 step 5, §6 "End to end" / "Named as unrun", §7.4, §8 "A progress observer" against `git diff cac3c70..HEAD`: missing, unasked, contradicted; told which text is a correction and which dirs are shared state it must not run against.
- [ ] **Step 5: Handoff** — what ran and what did not: the end-to-end test over the Python example; TRANSFORM compare and author; **NOT run: a rerun through Dymola or OpenModelica from the tool, and Windows — owner-run, once (§6)**; coverage; the predicates; the plan-versus-spec findings; carry-forwards (cancel — §9; the `lru_cache` on `overlay_loader._sibling_backend_index` is CLI-lifetime and a long-lived tool process that accepts into a sibling partition would serve stale overlays; `status.json` is per-invocation, so a partial rerun's dashboard shows the rerun subset; two jobs cannot queue). Nothing is pushed.
