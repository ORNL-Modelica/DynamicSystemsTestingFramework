# Run Timing Kept with the Results (step S2a-timing) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A test's Translate / Sim / Total times (and the run's own statistics) survive the run that measured them, so the Suite table, a `compare`, a reload in the tool and a dashboard written without `--report` all show them. Today the TRANSFORM gate read "—" in Sim (s) on every row of both surfaces; the owner named sim time as one of the things a row must show (feedback 2026-09-26, Suite point 2).

**Architecture:** One per-test file, `work_dir/<test_key>/run_stats.json`, written when a run records a test's result (beside `BatchManifest.record_result`, which every backend lane already calls) and read back by `Backend.read_results` when no in-process run result is at hand. It holds the run result's walls, elapsed and `statistics`. The timing block itself is built in ONE place for every backend (`run_timing`), not in Dymola's and OpenModelica's `read_result` only. The dashboard reads the same file for a row whose sidecar is absent.

**Tech Stack:** Python 3.10+, pytest, uv.

**Evidence (read at 50a3f4b, 2026-09-27):**
- Only Dymola (`dymola/backend.py:541-558`) and OpenModelica (`openmodelica/backend.py:304-321`) build `statistics["timing"]` (`translation_wall, sim_wall, other_wall, total_wall`, seconds, rounded to 2), and only when a `run_result` is in hand with a translation or sim wall; FMPy, Julia and the Python backend carry `sim_wall` / `elapsed` on their `TestRunResult` (`backends/types.py:198-203`) but never build a timing block. Failed runs skip `read_result` (`base.py:655-664`).
- `BatchManifest.save()` writes only the manifest entries; `load()` leaves `results` empty (`storage/batch_manifest.py:64-70, 160`), so `read_last_results` (`base.py:724-741`) calls `read_result(test, key, None)` — no walls, and for Dymola no dslog statistics either (they come from the run result). `--merge` has the same gap for merged-in tests (`pipeline.py:241-258`).
- `BatchManifest.record_result(result)` (`:72-101`) is called by every lane (`base.py:565/587`, `persistent.py:704`, `dymola/backend.py:183`) with the full `TestRunResult`.
- In-scope `test_NNNN/` dirs are wiped before a run (`pipeline.wipe_stale_state_for_scope`, `dymola/backend.py:117-121`); out-of-scope ones are kept — so a file inside the dir is exactly as fresh as the result beside it, for `--merge` and `--rerun` too.
- Dymola BATCH (`.mos`) never sets per-test walls; its `elapsed` is a batch average (`dymola/backend.py:327, 449`). It gets no timing block (as today), not a misleading one.
- status.json is not viable: each run rewrites it with only its own scope.
- Side effect to expect and accept: `compare --accept` stores `result.statistics` into the reference JSON (`reference_store.py:276-277`); restored statistics then reach a baseline exactly as `run --accept` in process does today.

---

## Rules for every task (the project's build loop)

As the other 2026-09 plans: commit ONLY with `git commit -F <own msg file> -- <paths>`; never `--amend` / `git stash` / `git checkout -- <path>`; never push; no `DSTF-NNN` id in a tracked file; FOREGROUND commands with an explicit `cd`; test-first; gates before each commit (task tests, `ruff check`, `ruff format --check`), before the last also the full `pytest -m "not playwright"`, `pytest -m playwright`, bare `mypy`.

### Task 1: One timing block for every backend

**Files:** `src/dstf/backends/common/result_variables.py` (beside `merge_timing_stats`), `src/dstf/backends/base.py` (`read_results`), `src/dstf/backends/dymola/backend.py:541-558`, `src/dstf/backends/openmodelica/backend.py:304-321`; Tests `tests/test_result_variables.py`, a new `tests/test_run_timing.py`.

- [ ] **Step 1: Failing tests.** `run_timing(rr)` returns `{translation_wall, sim_wall, other_wall, total_wall}` (seconds, rounded to 2, only the keys that are known; `other_wall = elapsed - translation - sim` when all three are known) for a run result with walls, and `{}` for one with neither a translation nor a sim wall (Dymola batch). `read_results` over an FMPy / Python-backend / Julia-style run result with `sim_wall=1.25, elapsed=1.5` gives `statistics["timing"] == {"sim_wall": 1.25, "total_wall": 1.5}`; a FAILED run result keeps its walls too. Dymola's and OpenModelica's existing timing tests stay green (their numbers are unchanged).
- [ ] **Step 2: Implement.** `run_timing` holds the block both backends duplicate; `Backend.read_results` merges `run_timing(rr)` into every result's statistics (success or failure) when `rr` is in hand; the two backends' `read_result` stop building it themselves (delete the duplicated block). OpenModelica's tool-reported `timing` keys (`frontend`, `simulation`, ...) are kept as they are — `merge_timing_stats` updates, it does not replace. Commit "backends: one timing block for every backend, built where results are read".

### Task 2: The run's statistics are kept with the result

**Files:** `src/dstf/storage/batch_manifest.py` (`record_result`) or a small `src/dstf/storage/run_stats.py`; `src/dstf/backends/base.py` (`read_results`); Tests `tests/test_run_timing.py`, `tests/test_run_prologue.py` / `tests/test_compare_accept.py` where `record_result` is exercised.

- [ ] **Step 1: Failing tests.** After a run of the Python example (`examples/python/PythonTestingLib`, as `tests/test_cli.py`'s run tests do) the work dir holds `test_NNNN/run_stats.json` per test with `{"translation_wall", "sim_wall", "elapsed", "statistics"}` (absent walls as null); `read_last_results` in a NEW process (no in-memory manifest results) returns each test's `statistics["timing"]` equal to the in-process one; a `--rerun` of one test leaves the other tests' files untouched and rewrites that test's; a test dir without the file (a run from before this change) reads back with no timing and no error; a corrupt file reads back with no timing and one warning line on stderr.
- [ ] **Step 2: Implement.** Write the file (atomically, as the manifest writes) where `record_result` records the result — the result carries its `test_key`; resolve the dir as the manifest does. In `read_results`, when `rr is None`, rebuild a minimal `TestRunResult` from the file (walls, elapsed, statistics) so the existing `read_result` code and Task 1's `run_timing` run unchanged — for Dymola this also restores the dslog `translation` / `simulation` statistics a reload lost. Commit "results: a test's run statistics and timing are kept beside its result".

### Task 3: The dashboard and the Suite table show them

**Files:** `src/dstf/reporting/dashboard_render.py` (`build_dashboard_context` / `_with_verdict`), maybe `src/dstf/cli/run_cmds.py`; Tests `tests/test_dashboard_render.py`, `tests/test_suite_rows.py`.

- [ ] **Step 1: Failing tests.** A final dashboard rendered WITHOUT `--report` (no sidecars) shows Translate / Sim / Total for a row whose `test_NNNN/run_stats.json` has them; a sidecar's walls still win where a sidecar exists (S1's order: sidecar first, then the comparison's verdict — the walls are the sidecar's); the Suite table's row shows Sim (s) after a reload of a Session over a work dir with the files (`suite_row` already reads `project.results[m].statistics`). 
- [ ] **Step 2: Implement.** In `build_dashboard_context`, a row with a `test_key` and no fresh sidecar reads `work_dir/<test_key>/run_stats.json` (small) and takes its walls through `run_timing`. Commit "dashboard: a page without --report shows each test's timing".

### Task 4: Record it; the gate

- [ ] `docs/decisions.md` D138 — "A run's statistics and timing are kept beside its result (2026-09-27)": the problem (the gate's "—"), the file, one timing block for every backend, Dymola batch's average not shown, the `compare --accept` side effect. `docs/usage.md`: what Translate / Sim / Total mean per backend (DSTF's wall clock around translate and simulate for Dymola persistent; omc's own record for OpenModelica; the simulate call's wall for FMPy / Julia / Python; none for Dymola batch). Full gates; commit.
- [ ] The controller runs an executing review of this plan first, a mutation review after, and a gate over a COPY of TRANSFORM: the stored TRANSFORM results were written before this change, so the gate RERUNS a handful of TRANSFORM tests on a copy (Linux Dymola 2026x, persistent) with `--filter` and checks their Sim (s) in the Suite table after a tool restart and on a dashboard written without `--report`; the other rows read "—" (old data) without error.

---

## Corrections 1 (executing review, 2026-09-27) — these OVERRIDE the task text above

An executing review built all three tasks in an archive (`/tmp/claude-1000/planrev-timing/arc`: 7bc3dc4 T1, a34d58e T2, 76a353e T3; gates green — 3125 passed / 21 skipped, playwright 87, mypy clean) and confirmed on REAL runs: the Python example, FMPy, OpenModelica (persistent and batch) and Linux Dymola 2026x persistent + batch (ModelicaTestingLib SimpleTest: a reload restores `[CPUtime, EventCounter, simulation, timing, translation]` with the file and only `[CPUtime, EventCounter]` without). Every backend's statistics JSON-dump without `default=`. 331 writes of the largest TRANSFORM-size payload (30 KB) cost 1.45 s total on /mnt/d with 4 threads (the manifest's per-result save already costs 5.07 s for 331). The archive is a REFERENCE; implementers still go test-first.

1. **MED (Task 1 Step 1).** No existing test pins the Dymola/OM timing block. FIRST add a characterization test — `DymolaBackend` and `OpenModelicaBackend`, `read_results` over a run result with `translation_wall=3.333, sim_wall=5.555, elapsed=10.004` and no .mat → timing `{translation_wall: 3.33, sim_wall: 5.55, other_wall: 1.12, total_wall: 10.0}`, and none for a run result with no walls. It must pass BEFORE and after the block moves.
2. **MED (Task 1).** When exactly one wall is known, `other_wall` is omitted (the old block counted the missing wall as 0 — it affects OM without an omc `simulation` time and Dymola translation failures, which never reached `read_result` before).
3. **MED (Task 2 Step 2).** Rebuild the run result only inside `_read_one` (items that reached the thread pool), with `success=True`; the verdict stays the manifest's (`run_verdicts`), as on a reload today. Out-of-scope manifest entries and "not found in discovery" entries read no file.
4. **MED (Task 2 Step 2).** `record_result` is the abort-proof verdict record: writing `run_stats.json` must never raise (catch OSError / ValueError / TypeError, print one `# WARN:` line). `json.dumps(..., default=str)`; write AFTER `self.save()`, outside `_write_lock`. `batch_manifest` imports `TestRunResult` only under TYPE_CHECKING, so a new `storage/run_stats.py` writes and reads plain dicts, and `backends/base.py` builds the run result (`kept_run_result(test_dir, model_id, test_key)`).
5. **MED.** Move Task 3's "the Suite table shows Sim (s) after a reload" test into Task 2 Step 1 (it has no red state in Task 3: the Session reload goes through `read_last_results`).
6. **MED (Task 4 / D138).** Restored Dymola translation statistics make structural-change warnings appear on `compare`, on tool reloads and on `--merge`d tests, as they already did on an in-process `run`. The verdict is unchanged; `n_warnings` and the `warnings` category change. Say so in D138; the TRANSFORM gate expects it.
7. **LOW (Task 2 file).** The file carries `"model_id"`: `{"model_id", "translation_wall", "sim_wall", "elapsed", "statistics"}`; a file written for another model is ignored with one warning.
8. **LOW (Task 1).** Build the timing block on a COPY: `merge_timing_stats` mutates `stats["timing"]` and OM's `dict(run_result.statistics)` is shallow, so the old code wrote into the run result's own statistics.
9. **LOW (Task 3 Step 2).** `enrich_row` returns the SAME row object when a guard refuses the sidecar: treat `row is not before` as "fresh sidecar applied"; otherwise, in final mode with a `test_key`, take the walls from the file through `run_timing` (a stale sidecar falls through to the file).
10. **LOW.** `cli/review.py:141` (`read_result(test, key, None)` after `[a]` with added variables) drops the restored statistics and timing from the accepted baseline: route it through `kept_run_result` + `run_timing`.
11. **LOW (Task 4 gate).** "The other rows read '—'" holds for the Suite table only; a no-`--report` dashboard of a `--filter` run shows only that run's rows even with `--merge` — a pre-existing gap the controller fixes as its own follow-up right after this plan (CLAUDE.md promises "a full merged report").
12. **LOW (D138).** Name the `compare --accept` git-diff noise from restored wall times (nondeterministic; `run --accept` already does it — 327 of 331 TRANSFORM baselines carry timing), and the 4 TRANSFORM baselines stored without translation/simulation/timing (ref_0195-0197, ref_0307), which gain them on a future `compare --accept`.
13. **LOW (Task 2 Files).** `tests/test_run_prologue.py` and `tests/test_compare_accept.py` need no change (the full suite passed with the file written).
