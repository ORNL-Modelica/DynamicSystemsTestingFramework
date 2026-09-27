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
