# Full code review — 2026-07-06

> **RESOLUTION (same day):** All findings fixed across 7 batches (A: verdict
> integrity, B: config safety, C: discovery, D: worker pool/backends, E:
> reference store, F: reporter round-trip/JS parity, G: CLI ergonomics), each
> with red-first regression tests — suite grew 768 → 985 passed (ruff + scoped
> mypy clean). Fix sites carry "review 2026-07-06" comments. Verified end-to-end
> against TRANSFORM-Library (326 tests, Dymola 2026x persistent workers):
> 326/326 simulated, 321/326 compare-passed, exit 1 — the 5 failures are real
> model regressions vs. old baselines (corroborated by structural warnings),
> not framework defects. Known residuals: .jl driver changes are text-pinned
> only (no Julia on this machine); Playwright browser tests not executed (not
> installed); finding 8/comparator keeps index-fallback for unnamed variables
> by design. Bonus fix found during Batch G: `-i` interactive mode crashed on a
> stale `dymola.mat_reader` import — every interactive invocation was broken.

Scope: entire `src/dstf/` package + Julia/Python driver scripts + browser templates
(~17k lines Python, ~5.6k lines JS/HTML). Method: seven parallel subsystem reviews
(comparison, CLI/config, discovery, simulator core, simulator backends,
storage/reporting, interactive JS), each finding required a concrete failure
scenario; the highest-severity claims were independently re-verified against the
source, and several were verified by execution. Baseline: `uv run pytest -m "not
playwright"` → **768 passed, 22 skipped, 0 failed** — none of the bugs below are
covered by the current suite.

Severity: **HIGH** = wrong verdicts, data loss, or hangs on realistic inputs.
**MED** = wrong behavior needing a slightly narrower trigger, or robustness holes
that abort whole runs. **LOW** = edge cases, misleading output, dead safety code.

Findings marked ✔ were re-verified by direct code read (and where noted, by
execution) after the subsystem review reported them.

---

## Theme 1 — Comparisons that pass when they should fail

The worst class for a testing framework: silent false PASS.

1. **HIGH ✔** `comparison/algorithms.py:155` — When every windowed segment of the
   reference is empty (window excludes all ref samples, or ref stored empty), NRMSE
   comparison returns `passed=True, nrmse=0.0`. Verified by execution: a window
   containing no ref sample passes against actual data that is wrong by ~1000×.

2. **HIGH ✔** `comparison/algorithms.py:378` — Points mode silently *skips*
   checkpoints whose time-box falls outside the actual trajectory
   (`if t_hi < t_lo: continue`) and passes if at least one other point scored. A
   simulation that died at t=40 passes a spec with checkpoints at t=20 and t=90.
   Verified by execution.

3. **HIGH ✔** `storage/reference_store.py:929-931` — `_downsample` trims the
   *sorted* index list from the end when event indices push the union over
   `max_points`, silently dropping the final point(s) — up to 25% of the tail with
   many events (verified numerically: baseline ending at t=74.77 instead of 100).
   All future comparisons only score the truncated range; tail regressions are
   invisible. Contradicts its own docstring ("Always preserves first, last...").

4. **HIGH** `simulators/julia/run_test.jl:86` and `run_persistent.jl:71` — Neither
   Julia driver checks `sol.retcode` or that the solve reached `stop_time`. A
   diverged/unstable/maxiters-truncated solve is written as `"success" => true`;
   with `--accept` the truncated trajectory becomes the regression baseline. Dymola
   and OM runners both guard this; Julia does not.

5. **HIGH ✔** `reporting/console_report.py:44-53` — `has_reference` is checked
   before `passed`, so a *failing baseline-free* test (range / declared-points /
   declared-peaks modes, D83) is classified NO_REF, `n_failed` stays 0, and the CLI
   **exits 0**. CI goes green on a real failure. Same masking in
   `junit_report.py:64` (emitted as `<skipped>`), `plot_comparison.py:1213`
   (dashboard badge "NO_REF"), and `cli.py:1164-1170` (`--rerun failed` /
   `-i failed` can't select them).

6. **MED** `comparison/algorithms.py:632` — Range mode passes vacuously with zero
   samples (window excludes everything → PASS). Verified by execution.

7. **MED ✔** `comparison/comparator.py:188-192` — Tolerance resolution is an
   `or`-chain, so an explicit `tolerance: 0.0` (strictest) is treated as unset and
   replaced by the looser default. Same falsy-drop at line 373. Also
   `cli.py:1526` — `--tolerance 0` / `--timeout 0` dropped by truthiness checks.

8. **MED ✔** `comparison/comparator.py:180` + `reporting/plot_comparison.py:576` —
   Legacy (non-tree) path pairs reference↔actual purely by numeric index, never by
   name. Insert/remove a variable in the spec without re-accepting and every
   downstream variable is scored (and plotted) against the *wrong* reference
   trajectory, labeled with the right name. Both sides carry `name` fields that are
   never cross-checked.

9. **MED** `comparison/algorithms.py:1027` — Dominant-frequency declared peaks
   don't claim matched actual peaks: two declared peaks can both match one actual
   peak, so a two-resonance system regressing to one resonance passes. (Contrast:
   event-timing's `claimed[]` logic at line 737 prevents exactly this.) Verified by
   execution.

10. **MED** `comparison/comparator.py:371` — `_test_is_baseline_free` only looks at
    `variable_overrides`; a mixed test (one range override + one plain tracked
    variable) is misclassified as baseline-free, so with no baseline stored it
    hard-FAILs instead of reporting NO_REF. Verified by execution.

## Theme 2 — Config/spec values silently ignored or destroyed

11. **HIGH ✔** `config.py:110-115` + `389` — A JSON syntax error in `testing.json`
    is swallowed (`except JSONDecodeError: pass` → `{}`), and the auto-create
    branch then **overwrites the user's testing.json** with a bare default config.
    A trailing comma destroys the simulators map, dependencies, reference_root,
    recognizers.

12. **HIGH ✔** `config.py:262` — The documented `testing.json` key `tolerance`
    (global comparison tolerance) is **never read** from the file; grep confirms
    the only occurrence is the dataclass default. All runs use 1e-4 regardless.

13. **HIGH ✔** `discovery/test_registry.py:210-218` — The spec-over-annotation
    merge uses *equality with framework defaults* as the "explicitly set" sentinel
    for `stop_time`/`tolerance`/`method`. A spec that explicitly sets
    `stop_time: 1.0` (== default) against `experiment(StopTime=100)` silently runs
    100 s. Neighboring fields correctly use `is not None`; these three fields need
    None-defaults in `TestModel` (test_registry.py:32-34).

14. **MED** `config.py:316-317` — `--config` pointing at a nonexistent path is
    silently ignored; the run proceeds on auto-detected defaults (and may write a
    fresh default testing.json).

15. **MED** `config.py:500-502` — Relative `work_dir` resolves against CWD while
    every other testing.json path resolves against the config file's directory;
    `compare` from another directory finds no results and exits 1.

16. **MED** `discovery/spec_parser.py:117-126` — JSON `null` for
    `stop_time`/`tolerance`/`output_interval`/`timeout` crashes discovery with
    `TypeError: float(None)` — and the module's own docstring shows
    `"output_interval": null` as the example.

17. **MED** `simulators/openmodelica/mos_generator.py:36` + `config.py:483-486` —
    The documented `"dependencies": ["Modelica"]` bare-name form is absolutized by
    config.py before the classifier can see it, producing `loadFile()` on a
    nonexistent path *and* suppressing the MSL auto-injection. Bare library names
    likely never work through testing.json for OM/Dymola persistent workers.

18. **MED** `config.py:411-413` — Simulator auto-detect overwrites a CLI-supplied
    `--simulator-path` when `--simulator` isn't also given.

19. **MED** `config.py:125-126` — `_create_default_config` crashes
    (FileNotFoundError, no mkdir) when the target dir doesn't exist — e.g. first
    run with a fresh `--reference-root`.

## Theme 3 — Worker pool / run lifecycle

20. **HIGH ✔** `simulators/base.py:1067-1082` — A worker whose restart budget is
    exhausted stays in the dispatch loop, tight-looping on the shared queue and
    instantly fail-marking tests healthy workers could have run (~half the
    remaining suite with 2 workers).

21. **HIGH ✔** `simulators/base.py:1091` — `_worker_loop` wraps
    `run_test_with_timeout` in try/**finally** with no `except`, but the Worker ABC
    contract explicitly permits raises. A raise kills the dispatch thread: the
    in-flight test is never recorded (dashboard shows it "running" forever), and
    with `--parallel 1` the rest of the queue is silently dropped while the run
    "completes" and reports a summary.

22. **HIGH** `simulators/dymola/runner.py:320-321` — Batch timeout path does
    `proc.kill(); proc.communicate()`. Killing dymola.exe doesn't kill its dymosim
    child, which inherited the pipes, so `communicate()` blocks until dymosim exits
    — the timeout path itself hangs indefinitely (verified: post-kill communicate
    blocks on a grandchild holding the pipe).

23. **HIGH** `simulators/openmodelica/runner.py:175` (also `julia/runner.py:181`,
    `python/runner.py:168`) — `subprocess.TimeoutExpired.stdout` is **bytes** even
    with `text=True` (verified on 3.12); `(exc.stdout or "") + "\n..."` /
    `write_text(bytes)` raises TypeError inside the timeout handler, which
    propagates through `future.result()` and aborts the entire run — precisely when
    a test times out after printing output.

24. **MED** `simulators/base.py:885` — Workers that fail during `start()` are never
    `close()`d; if all fail, `run_tests` raises before the cleanup loop → N
    orphaned Dymola processes each holding a license seat.

25. **MED** `simulators/progress.py:153` — `_write` snapshots state *outside*
    `_write_lock`, so a stale snapshot can overwrite a newer status.json and the
    live dashboard shows regressed state until the next event.

26. **MED** `simulators/openmodelica/persistent_runner.py:217` — Persistent OM
    worker calls `OMCSessionZMQ()` with no args, ignoring the configured omc path
    that batch mode honors — silently running a different omc version against the
    same baseline partition.

27. **MED** `simulators/julia/run_persistent.jl:43` — Persistent worker never
    checks `isdefined(:build_mtk_system)` after `include`; a file that fails to
    define it silently reuses the *previous test's* model (batch mode checks this).

28. **MED** `simulators/__init__.py:79` — The PERSISTENT_WORKERS capability-honesty
    check compares classmethod objects with `is`, which is always False — the
    validation is inert (and FmpyRunner already exhibits the drift it was meant to
    catch).

29. **MED** `simulators/python/run_test.py:94` + `python/runner.py:281` — Driver
    validates list lengths but not value types; a user `simulate()` returning
    string values passes the run, then `np.asarray(..., float64)` raises during
    `read_results` and aborts the whole read phase for all tests.

30. **MED** `cli.py:334-337` — `run --accept` returns 0 even when simulations
    failed and only a subset of baselines was stored; combined with `--rerun`
    (which force-enables `--merge`, cli.py:262), `accept_results` re-stores
    baselines for *all* manifest tests from stale on-disk results, not just the
    rerun subset.

## Theme 4 — Reference store integrity

31. **MED** `storage/reference_store.py:792` (also 466, 586, 641, 820) — All
    reference writes are plain `write_text` (non-atomic). A Ctrl-C mid-accept
    corrupts the baseline; the next scan silently drops it, and `next_id()` then
    re-allocates that ID to a different model, clobbering the file.
    `dashboard_render._atomic_write` already implements the right pattern.

32. **MED** `storage/reference_store.py:258` + `cleanup_obsolete` — After deleting
    the highest-ID obsolete ref, `next_id()` (max-over-scan) reuses that ID, and
    the deleted ref's orphaned `soft_checks/ref_NNNN/` + `companions/ref_NNNN/`
    directories attach to the new, unrelated model.

33. **MED** `storage/reference_store.py:261` — No cross-process locking and no
    index revalidation: two concurrent `--accept` runs on the same partition
    allocate the same ID; the second silently overwrites the first model's
    baseline.

34. **MED** `storage/reference_store.py:572` vs `reporting/overlay_loader.py:363` —
    `companion add` stores relative paths verbatim (CWD-relative at add time) but
    the loader resolves them against `ref_dir` — relative companions register fine
    and then always load as "missing".

35. **LOW** `storage/reference_store.py:448` — soft_check/companion names are
    interpolated into paths unvalidated; a name containing `/` writes outside the
    role dir and becomes invisible to list/remove/`against`.

36. **LOW** `storage/reference_store.py:230` — Duplicate `model_id` across ref
    files: silent last-wins in `_by_model`, older baseline stays active on disk,
    zero warnings.

## Theme 5 — Interactive report: live scores ≠ CLI scores; exports lose edits

The reporter-as-IDE loop (tune → export patch → `spec-update`) has several breaks.

37. **HIGH ✔** `reporting/templates/interactive.js:3266-3272` — On page load the
    report re-scores every leaf **from the LTTB-decimated arrays** and overwrites
    the CLI verdicts (pills, per-variable status, summary) with no disclosure —
    directly contradicting decimate.py's contract ("affects what the browser
    draws, not pass/fail scoring"). Borderline tests flip verdicts in the browser
    before the user touches anything. (Also `plot_comparison.py:1096`: users tune
    tolerances against decimated-data scores that the full-data CLI won't
    reproduce.)

38. **HIGH ✔** `interactive.js:55-61` + `4707-4709` — `params` and
    `original_params` are shallow copies sharing the same nested arrays
    (`tube_points`, `points`, `events`, `peaks`). All in-place edits (drag a tube
    point, edit a checkpoint field) are invisible to the `cur === o` export diff:
    **the exported patch silently omits the user's tuning**, and the reset button
    can't restore arrays either.

39. **HIGH ✔** `interactive.js:1302` vs `1581` vs `comparison/modes.py:104` —
    Width-mode token mismatch: Python/spec/dropdown say `'abs'`, editor internals
    only recognize `'absolute'`. Literal-y-bounds tubes are unusable in the editor
    (snap back to rel), and spec-authored `'abs'` tubes render/score as rel —
    guaranteed JS-vs-CLI verdict divergence.

40. **HIGH** `interactive.js:238-271` — The tube live scorer ignores
    `tube_width_mode` (and `tube_interpolation`) whenever `tube_points` exist —
    every control-point value is scored as a band offset. The editor *seeds*
    rel-mode points (line 1381), so the pill and the CLI disagree by a factor of
    |ref| immediately. The parity test (`tests/test_scorer_parity.py`) only covers
    the scalar-rel no-points path.

41. **HIGH** `interactive.js:4711` + `discovery/patch_apply.py:205-219` — Per-leaf
    patch ops target `/metrics/children/N/...`, but for tests defined via flat
    `comparison.variable_overrides` (no `metrics` block — the primary documented
    format) `patch_apply` auto-creates the missing parents as *dicts* (`"children":
    {"0": ...}`), the tree validator rejects it, and `spec-update` aborts. **No
    scalar edit on a flat-override test can round-trip.**

42. **MED** `interactive.js:147` — NRMSE constant-signal normalization diverges
    from Python (`range > 0 ? rmse/range : rmse` vs Python's `_EPS`-guarded
    magnitude normalization) → false FAIL pills on constant signals with float
    noise. Related: line 134, JS skips Python's event-boundary segmentation,
    inflating live NRMSE across discontinuities (bouncing-ball-type signals).

43. **MED** `interactive.js:196-202` — Points live scorer passes when zero points
    scored (Python requires `scored > 0`); clipped-away checkpoints show PASS in
    the browser and FAIL in the CLI.

44. **MED** `interactive.js:829` — Live FFT runs on the decimated trajectory →
    different pow-2 grid and bin spacing than the CLI's full-resolution FFT;
    declared peaks with tight tolerances match in CLI but not live (and
    dominant-frequency is not flagged CLI-authoritative).

45. **MED** `interactive.js:3538` — Combinator param edits (k, threshold, weights)
    mutate the pristine TREE_VIEW node when they're the first structural edit; the
    working-tree clone still has the old value, so the input visibly reverts and
    the export carries the stale value. (Other mutations correctly resolve via
    `findWorkingNode`.)

46. **MED** `interactive.js:1488` — Unsynced per-side tube width modes only affect
    how dragged values are *stored*; scoring/polygon/export interpret everything
    under the single global mode → nonsense tubes that export wrong.

47. **MED** `interactive.js:917` — `_detectEvents` emits one event per duplicate
    *pair*; Dymola's 3-duplicate events seed two identical declared rows (Python
    groups runs of duplicates into one boundary).

## Theme 6 — Discovery / parsing

48. **MED** `discovery/mo_parser.py:222` and `:48` (+ `json_recognizer.py:430,
    493`) — `_parse_experiment` and `_extract_model_name`/`_extract_within` run on
    raw text: a `// experiment(StopTime=5)` comment or the word "model" in a
    comment above the class shadows the real annotation/name, silently yielding
    wrong sim parameters or a bogus model_id. (`_parse_unit_tests` already strips
    comments/strings first — the helpers just don't use it.) The `[^)]*` capture
    also truncates at the first `)`.

49. **MED** `discovery/patch_apply.py:165-168` — `add` at an existing array index
    *replaces* instead of inserting (RFC 6902 violation, silently destroys a
    sibling leaf); out-of-bounds index raises raw IndexError instead of PatchError
    (uncaught in `cmd_spec_update`).

50. **MED** `discovery/json_recognizer.py:238` — The `parameter` field source
    extracts from literal-*stripped* text, so string-valued parameters
    (`algorithm="Dassl"`) can never be extracted; the quoted-string branch of
    `_extract_param_value` is unreachable.

51. **LOW** `discovery/patch_apply.py:101` vs `test_registry.py:195` — With
    duplicate `model` entries in test_spec.json, patching edits the FIRST match
    while discovery uses the LAST → applied patches silently ineffective.

52. **LOW** `discovery/spec_parser.py:180-185` — `add` with overwrite replaces the
    whole entry with `{model, variables}`, silently deleting hand-authored
    `metrics`/`comparison`/`simulation`.

53. **LOW** `discovery/spec_parser.py:60-61` — A non-dict entry in `tests` crashes
    discovery with AttributeError instead of skip-with-log (same in
    add/update helpers).

54. **LOW** `discovery/spec_parser.py:259-261` — `update_test_comparison` replaces
    the comparison section wholesale despite its docstring promising a merge —
    legacy tolerance patches drop `variable_overrides`.

55. **LOW** `discovery/mo_parser.py:272` — Multi-class files (single-file package
    storage) collapse to one test attributed to the first class keyword; remaining
    tests silently undiscovered.

56. **LOW** `discovery/mo_parser.py:230-232` — `StopTime=0.5*3600` truncates to
    0.5; garbage captures crash discovery with uncaught ValueError.

57. **LOW** `discovery/test_registry.py:234-241` — Spec-only tests stamp all five
    sim fields with provenance "test_spec" even when they're framework defaults —
    the dashboard's resolution explainer lies.

## Theme 7 — CLI ergonomics / exit codes

58. **MED** `cli.py:1192` — The `-i` review-filter string is validated only *after*
    the full simulation run; a typo (`sim_failed`) burns hours then dies with an
    uncaught traceback, and `compare` has no `-i` to recover.

59. **MED** `cli.py:267-269` — `--rerun failed` with nothing to rerun prints "No
    tests matched the filter." and **exits 1** — a fully green prior run fails the
    CI job and skips the merged report.

60. **MED** `cli.py:470-471` — `export --output <path>` crashes with
    AttributeError (str passed where ReferenceStore expects Path).

61. **LOW** `cli.py:334-338` — `--accept` silently wins over `-i` (if/elif order):
    the review the user asked for never happens and everything is accepted.

62. **LOW** `cli.py:1354-1363` — Interactive-review exit codes inverted at the
    edges: reviewing all-failing tests and skipping all returns 0; quitting a
    green suite without accepting returns 1.

63. **LOW** `cli.py:45` — `fnmatch.fnmatch` is case-insensitive on Windows,
    case-sensitive on Linux — same filter selects different tests across the two
    OS partitions the project maintains. Use `fnmatchcase`.

64. **LOW** `cli.py:30` — Comma-splitting `--filter` breaks fnmatch character
    classes containing a comma (`Test[A,B]*`) with no escape mechanism.

65. **LOW** `cli.py:41` — `--package MyLib.Fluid` also matches
    `MyLib.FluidExperimental` (raw startswith, no dot boundary).

66. **LOW** `cli.py:61` + `base.py:198-212` — batch_manifest.json is written
    non-atomically and loaded unguarded: a torn write makes every subsequent
    run/compare crash at startup until hand-deleted.

67. **LOW** `config.py:26` — "Python" missing from SIMULATOR_BACKENDS, so
    `"simulator": "Python 3.12"` (the documented versioned-name convention) fails
    with "Unsupported simulator backend".

## Theme 8 — Remaining reporting/serialization

68. **MED** `reporting/plot_comparison.py:1091` — `comparison_data.json` (the
    documented downstream artifact) is dumped with `allow_nan=True`; any
    missing-baseline variable injects literal `Infinity`/`NaN` → invalid JSON for
    every strict consumer. Same for stored baselines
    (`reference_store.py:947`).

69. **LOW** `reporting/html_report.py:14` — Summary counters double-count: NO_REF
    counted as passed, SIM_FAIL as failed; header numbers don't reconcile with row
    statuses.

70. **LOW** `reporting/junit_report.py:34` — `<skipped>` children emitted but no
    `skipped="N"` attribute; attribute-driven CI parsers tally no-baseline tests
    as passed.

71. **LOW** `reporting/plot_comparison.py:1165` — Overlays on `nobaseline`
    trajectories are never decimated → multi-MB HTML exactly in the sibling-backend
    cross-check case.

72. **LOW** `reporting/schema_export.py:122` — The six per-mode `$defs` are emitted
    but never `$ref`'d (leaf spec allows `additionalProperties: true`); the schema
    validates nothing mode-specific, and the defs' `additionalProperties: false`
    would reject real leaves if wired.

73. **LOW** `simulators/fmpy/runner.py:197` — FMPy timeout result omits
    `timed_out=True` (progress reporter gets it, summary doesn't) → dashboard and
    console disagree. Also line 229: `_save_result` outside the try block —
    OSError aborts the run without finalizing progress.

74. **LOW** `simulators/common/mat_reader.py:156` — Aclass is never read;
    "binNormal" dsres files decode as transposed garbage returned as success
    (user-supplied .mat baselines). Line 199: dataInfo column 0 → index −1 wraps
    to the *last* row — wrong variable's data under the requested name.

75. **LOW** `simulators/julia/persistent_runner.py:98` (and batch runners) — pipes
    opened `text=True` without `encoding="utf-8", errors="replace"`; on cp1252
    Windows a UTF-8 Julia backtrace kills the reader thread → every subsequent
    test on that worker times out. Also `julia/runner.py:212` +
    `python/runner.py:194`: whitespace-only stderr → IndexError.
    `julia/run_test.jl:30`: failure JSON built with Julia `repr` escaping (`\$` is
    invalid JSON) → real error detail lost.

76. **LOW** Various interactive.js: cleared windows resurrected on structural
    export (4401); peak tolerance default 0 vs Python 0.01 (309); brush mode never
    disarms without a completed selection (3223); WeakMap leaf-identity miss leaks
    document-level listeners (3229); added leaves never get a status pill (3837);
    passthrough textareas corrupted to "[object Object]" on sibling sync (4510);
    clipboard copy has no fallback on file:// (4752); integer-like variable names
    reorder `Object.keys` and desync plot IDs (15). `dashboard/cross_backend`:
    ValueError from companion-name collision aborts the run post-simulation
    (cross_backend.py:166); `export_fmu` cleanup bypasses the hard-kill safety net
    (dymola/persistent_runner.py:801); Dymola batch timeout marks already-completed
    tests in the batch as timed out (dymola/runner.py:322).

---

## Suggested triage order

1. **Verdict integrity** (items 1–10): these make the framework report green on
   real regressions. The empty-slice family (1, 6) shares one fix: treat "nothing
   to compare" as a hard failure with a clear message, not a vacuous pass. Item 5
   is a one-line reorder (`passed` before `has_reference`) plus a distinct
   "failed, baseline-free" status.
2. **Data destruction** (11, 3, 31–33): config overwrite and non-atomic baseline
   writes; both have existing in-repo patterns to copy
   (`dashboard_render._atomic_write`).
3. **Silent config loss** (12, 13): both are small, high-leverage fixes.
4. **Worker pool** (20–23): add an `except` + restart-exhausted thread exit; use
   process groups (`start_new_session=True` + `os.killpg`) for batch timeouts.
5. **Reporter round-trip** (37–41): deep-copy `original_params`, fix the
   `'abs'`/`'absolute'` token, surface "scores recomputed on decimated data" in
   the UI, and make `spec-update` synthesize the implicit tree before applying
   per-leaf ops.
