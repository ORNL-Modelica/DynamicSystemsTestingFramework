# Session handoff — stale-state fix + dashboard UX polish + CLI cleanup + 100% TRANSFORM Linux Dymola

**Date**: 2026-05-01
**Covers**: 5 user-reported issues addressed in 4 commits on top of prior session HEAD `a9be593`. Plus field-verification of the stale-state fix on TRANSFORM (326/326 PASS, up from 319/326 in the prior session).

**State at HEAD** (commit `a79241d`):
- **861 unit tests passing + 3 expected skips, 0 regressions** (full suite ~4½ min on Linux WSL).
- **TRANSFORM-on-Dymola-Linux: 326/326 PASS** (44-min wall, 0 failed, 0 timed out, 0 cascade events). Prior session's 97.85% with 5 timeouts is now 100% — the stale-state fix + cascade fix + 240s timeout overrides + accepted Linux Dymola baselines together produce a clean run.
- **ModelicaTestingLib gains Linux Dymola baselines** — 11 ref_NNNN.json (SimulateOnlyTest stores no ref). Demo library now has all four partitions: Dymola/{windows,linux} + OpenModelica/linux.
- **5 simulator backends, 3 test libraries** unchanged.

---

## What landed

| Commit | Issue | Change |
|---|---|---|
| `c6e8a04` | #5 stale-state | `_wipe_stale_state_for_scope` in cli.py: `cmd_run` wipes `<work_dir>/reports/<report_dir>/` AND `<work_dir>/<test_key>/` for in-scope tests; `cmd_compare` wipes only the report dir (preserves sim outputs to re-evaluate). Defensive guards in `dashboard_render._enrich_row_from_comparison`: skip enrichment if `summary["model_id"] != row["model_id"]` OR `summary["written_at"] < snapshot["start_wall"]`. Both fire stderr warnings so silent overrides become visible. Sidecar's `summary` block now stamps `written_at` + re-affirms `model_id`. 10 new tests. |
| `da47515` | #1 + #2 + #3 dashboard UX | (1) `#sticky-chrome` wraps h1 + status bar + progress bar + counter pills; thead's two rows stack via `--chrome-height` + `--header-row-height` CSS variables driven by `ResizeObserver`. (2) Drag handles on every header th update `th.style.width`; for Model + Detail (truncated columns) drag also updates `--col-w-<key>` so the td's max-width follows. Widths persist to localStorage alongside filter / selection / sort. (3) meta-refresh `2s → 5s` + status-bar copy + JS comments + tests. |
| `93d2a81` | #4 CLI cleanup | `dstf --version` (-V) reading `importlib.metadata`; symmetric `check-openmodelica` + `check-julia` subcommands (probe OMPython + omc binary; probe `julia --version`); `--simulator` / `--simulator-path` / `--work-dir` added to `compare` (filling the asymmetry vs `run`); `--report` help fixed to match unified-dashboard reality; `soft-check` action help explicit about why there's no `add` (D66 — soft_checks come from `import-baseline` only); removed bogus `manifest rebuild` from `docs/usage.md` (the index is built fresh on every command run, nothing to rebuild). |
| `a79241d` | NO_REF observation | After the user ran ModelicaTestingLib on Linux Dymola and saw NO_REF for every test (except SimulateOnlyTest), diagnosis confirmed it wasn't a regression — `Dymola/linux/` partition simply didn't exist for that demo lib (only `Dymola/windows/` was committed). `--accept` produced 11 baselines; re-run shows 12/12 PASS in the dashboard. |

Total: **5 user-reported issues + 1 demo-lib gap closed in 4 commits.**

---

## Stale-state fix — the bug + design

**The bug** (root cause): `cmd_run` never wiped `<work_dir>/reports/<report_dir>/` before re-running tests. Stale `comparison_data.json` sidecars from a prior `--report` run persisted on disk. The dashboard's `_enrich_row_from_comparison()` then copied their `status_text` / `status_class` into the fresh row — overriding a passing sim with the prior run's FAIL verdict. Exact reproduction of the user's reported `check_Alphas_F1D` symptom.

**Two-layer fix**:

1. **Wipe contract**. `_wipe_stale_state_for_scope(config, tests, ref_id_map, *, wipe_sim_dirs)` clears per-test sim and report dirs for the tests this invocation is about to (re-)process. Out-of-scope tests' dirs are untouched — that's what makes `--merge` and `--rerun` keep working. Replaces the inconsistent per-runner wipes (Dymola batch + Dymola persistent + OM persistent had them; Julia, Python, FMPy, OM batch did not — stale-state risk on 4 of 7 paths).
2. **Defensive double-entry bookkeeping** at the dashboard enricher. `summary.model_id` mismatch with `row.model_id` (bookkeeping drift) → skip + warn. `summary.written_at < snapshot.start_wall` (stale sidecar from before this run started) → skip + warn. Sidecars without `written_at` (legacy) pass through normally.

**Field verification** on TRANSFORM Linux Dymola, 4 scenarios:

| Scenario | Outcome |
|---|---|
| Fresh run with `--report` (3 tests) | All sidecars stamped with `model_id` + `written_at`; dashboard reads correctly |
| Inject stale sidecar (older `written_at` claiming FAIL) | Timestamp guard fires; dashboard shows live PASS, not planted FAIL |
| Re-run with stale sidecar present | Wipe clears it; fresh sidecar replaces; both sim_dir + report_dir rebuilt |
| Inject wrong-model sidecar (bookkeeping drift) | Model_id guard fires; row stays PASS |

Then **full TRANSFORM 326-test rerun**: 326/326 PASS, 0 failed, 0 timed out, 0 cascade events.

---

## Dashboard UX polish

Three issues, one commit (`da47515`):

**Sticky chrome (#1)** — h1 + status-bar + progress-bar + counter-pills wrapped in `#sticky-chrome` at `top: 0`. Two thead rows stack below at `top: var(--chrome-height)` and `top: var(--chrome-height) + var(--header-row-height)`. JS `ResizeObserver` on `#sticky-chrome` keeps the variables fresh as counter pills wrap or chrome content changes.

**Resizable columns (#2)** — `<span class="col-resize-handle">` on the right edge of every header th. mousedown starts a drag that updates `th.style.width`; for the Model + Detail columns (which use ellipsis truncation), drag also updates `--col-w-<key>` so the td's max-width follows. The hardcoded `max-width: 28em` cap on `.model-cell` is gone — users who want to read long fully-qualified model IDs drag the Model column wider. Widths persist to localStorage alongside filter / selection / sort.

**Refresh interval (#3)** — meta-refresh `content="2"` → `content="5"`. Two seconds was interrupting users mid-interaction; five seconds is still tight enough for live feedback.

---

## CLI cleanup

Six fixes in `93d2a81`, applied from the audit's punch list:

- `dstf --version` (-V) via `importlib.metadata.version("dstf")`. Pyproject.toml stays the source-of-truth.
- `dstf check-openmodelica` and `dstf check-julia` for symmetry with `check-dymola`. OM probe describes OMPython availability + `omc` binary; Julia probe checks `julia --version`. Useful for diagnosing persistent-worker preflight failures.
- `compare` gains `--simulator`, `--simulator-path`, `--work-dir` (already on `run`).
- `--report` help text matches unified-dashboard reality (dashboard always renders; flag only controls per-test deep dives).
- `soft-check` action help explicit about D66's intentional add-via-import-baseline-only design.
- Removed `manifest rebuild` from `docs/usage.md` (the index is built fresh on every command run; nothing to rebuild).

---

## NO_REF on ModelicaTestingLib + Linux Dymola — diagnosis + fix

User ran `dstf --config examples/.../testing.json run --report` on Linux Dymola and saw NO_REF for every test (except SimulateOnlyTest). Diagnosis: not a regression — `Dymola/linux/` partition simply didn't exist in the demo lib. Only `Dymola/windows/` was committed; TRANSFORM had been given Linux Dymola baselines during the prior cascade-fix arc, but ModelicaTestingLib hadn't. `--accept` on Linux Dymola produced 11 baselines (`a79241d`). Re-run shows 12/12 PASS.

**Lesson**: when adding a new OS or backend, demo libraries need their own `--accept` pass, not just project libraries. Reference partitioning is enforced; cross-partition fallback is not done by design (different backends produce different solver outputs).

---

## Cross-OS validation (this session)

- **Linux WSL Dymola**: TRANSFORM 326/326 PASS verified end-to-end at HEAD `a79241d`. ModelicaTestingLib 12/12 PASS with newly-accepted baselines.
- **Windows side**: not exercised this session. The dashboard / CLI changes are file-IO + JS / argparse — no platform-specific code paths added. Should match Linux behavior.
- **OpenModelica + FMPy + Julia**: not exercised this session (no env access). The wipe contract + sidecar guards are backend-agnostic; the runner-internal wipes are unchanged so persistent OM behavior is preserved.

---

## Pre-session sanity

```bash
git log --oneline -10                                         # HEAD = a79241d
uv run pytest -q --ignore=tests/test_interactive_html_snapshot.py   # expect 861 passed + 3 skipped
export PATH="$HOME/.juliaup/bin:$PATH" && uv run pytest -q     # same on Julia-installed envs

# Smoke each backend; verify dashboard.html exists at <work_dir>/dashboard.html, opens in browser:
uv run dstf --config examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json run --simulator Dymola
uv run dstf --config examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json run --simulator OpenModelica
uv run dstf --config examples/julia/JuliaMtkTestingLib/Resources/ReferenceResults/testing.json run
uv run dstf --config examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json run
uv run dstf --config examples/fmu/testing.json run

# New diagnostic commands:
uv run dstf --version
uv run dstf check-openmodelica
uv run dstf check-julia
```

Working tree clean. Filter / selection / sort / column widths all persist via localStorage keyed on file path.

---

## Candidate next moves

Roadmap items #1–#6 + #8 closed. **#7 (capabilities) still pending** — Dyad, Julia recognizer, MTK FMU export, experiment alignment. None touched this session.

### Top-of-stack candidates

**A-tier (closing-out housekeeping)**

- **Strip the redundant in-runner rmtrees** — DymolaRunner (runner.py:147-150), DymolaWorker (persistent_runner.py:282-283), OpenModelicaWorker (persistent_runner.py:304-305) all wipe `test_dir` themselves. cmd_run's centralized wipe makes them defensive duplicates. **Decision deferred this session: keep them.** Tightening the run_tests contract (callers MUST pre-wipe) for ~10 LOC is not net-positive — the in-runner wipes are O(0) on already-empty dirs and protect against any future caller of `runner.run_tests` outside cmd_run. Could revisit if test_openmodelica_persistent.py:619 (the one direct caller) gets refactored.
- **Sim-dir model_id stamping (B4)** — originally scoped as "stamp model_id into simulation_artifacts.json" but that file doesn't exist. Inventing a new `.dstf-test-meta.json` to defend against theoretical sim-dir bookkeeping drift is unjustified — cmd_run wipe + sidecar model_id guard + batch_manifest's stable test_key→model_id mapping cover the vectors we know about. Re-scope only if a real bookkeeping-drift case appears.

**B-tier (#7 capabilities — each is its own session)**

- **Julia source recognizer** (~1 day) — auto-discover Julia tests from `.jl` files, mirroring the Modelica recognizer. Closes a real D81 gap that's been pending since the rename.
- **MTK FMU export via `ModelingToolkit.generate_fmu`** (~1 day) — wires Julia into the `Capability.FMU_EXPORT` cross-backend chain.
- **Dyad validation** (~½ day) — port a sample Dyad test, confirm the existing MTK runner handles it (it should — Dyad compiles to MTK).
- **Experiment-data alignment preprocessing** (ideas.md #57, ~3-5 days) — needs a concrete user use case before scoping; pure capability work without a driving need is bait for over-engineering.

**C-tier (smaller follow-ups)**

- **Dymola batch FMU export** (`TODO(batch-fmu-export)` marker). ~30-50 LOC `.mos` script work; deferred until a batch-only codebase needs it.
- **Persistent-worker Python** (ideas.md #58). Mirrors Julia D77→D78. Defer until perf ceiling hits.
- **OM FMU export** via `buildModelFMU`.
- **`_find_top_n_peaks` JS parity** (~30 min) — closes the last scoring-related JS↔Python drift surface.
- **`reference_store.py` review** (~1-2 hr) — 933 lines but coordinated; less obvious clean seam.
- **Visual-regression Playwright screenshots**.
- **Phase 9 dataset types** (E-tier foundational): Events / Spectrum / Distribution / Scalars / Field.

---

## Plan-quality lessons (carried forward)

Worth carrying forward:

1. **Audit before scoping.** Two B-tier tasks this session collapsed when audited: B4 (stamp simulation_artifacts.json) was scoped against a phantom file; B3 (strip redundant rmtrees) was scoped as ~20 LOC savings but the real cost is contract tightening, not LOC. Lesson: when a follow-up is presented as "we should also do X," the first move is `grep` + targeted read to confirm X is real and the cost story holds.

2. **NO_REF is correct, not a bug.** When the user reported "NO_REF for every test," reflexive impulse was to look for a regression in the wipe code. Real diagnosis: the demo lib simply lacked Linux Dymola baselines. Lesson: when behavior looks wrong, check the data first (does the partition exist?), the code second.

3. **Multi-layer defense is cheap on simple cases.** The stale-state fix layers wipe + timestamp guard + model_id guard. Each is ~5-10 lines. Together they make the bug literally unable to recur silently — even if a future code path bypasses the wipe, two independent guards catch it. Cheap insurance.

4. **Field verification > unit verification.** Unit tests proved the bug fix. The 4-scenario field check (real Dymola subprocess, real sidecars, hand-injected stale state) caught no new bugs but built confidence the integration was correct. Then the full 326-test TRANSFORM rerun closed the loop end-to-end.

5. **Carried from prior sessions**: TDD-shaped subagent dispatch works for plan execution; capability declarations need mechanical enforcement; sentinel-as-default is a bug not a style choice; read vision/usage docs before recommending architectural alternatives.

---

## Known limitations (deferred by design)

Updated from prior session — no new entries this session:

| Item | Why | Workaround |
|---|---|---|
| Event-timing live JS scorer | Event-pairing algorithm non-trivial; CLI authoritative | Pill shows CLI result until next CLI rerun |
| **Dymola batch FMU export** | `.mos`-script implementation pending; persistent mode works via DymolaInterface | Use persistent mode (drop `--batch`) for cross-backend chains |
| `_find_top_n_peaks` JS↔Python parity | Authoring-UX correctness only; doesn't affect verdicts | Skip until a real drift case appears |
| `renderModeControlsHtmlJs` parity | HTML-string parity testing is brittle | Trust Python golden-file snapshots |
| Tube per-point-per-side width modes | JS UI stores; polygon uses global mode | Use synced mode |
| Window brush one-shot per activation | UX choice | Click brush again to redo |
| Multi-select wrap in tree editor | Deferred | Wrap single, then move siblings |
| JuliaRunner FMU export | `MTK.generate_fmu` not wired | Run directly via Julia runner |
| Persistent-worker Python | D77→D78 progression not yet applied | Subprocess-per-test sufficient for typical suites |
| Dyad tests | Untested (should work — compiles to MTK) | Port a sample when concrete need arises |
| Bug 3 reproduction (Plotly autorange-stuck) | Doesn't reproduce via state-mutation path | Characterization test guards current behavior |
| Points mode live edge mirror during drag | Plotly doesn't fire `plotly_relayouting` for shape edits | Snap-on-release accepted |
| ModelicaTestingLib EventTest / IntervalTest / NoUnitTest / SimulateOnlyTest on Julia | Deferred | — |

---

## Starter prompt for the next session

> Resuming DSTF (Dynamic Systems Testing Framework) at commit `a79241d` on `main`. Prior session was a 4-commit closeout pass on user-reported issues:
>
> - **Stale-state bug fixed and field-verified** — `cmd_run` now wipes `<work_dir>/reports/<report_dir>/` + `<work_dir>/<test_key>/` for in-scope tests; `cmd_compare` wipes only the report dir. Sidecars carry `written_at` + `model_id`; the dashboard enricher refuses stale or mismatched sidecars (warns to stderr). 4 field scenarios + full 326-test TRANSFORM Linux Dymola = 100% PASS.
> - **Dashboard UX**: sticky chrome (h1 + status + bar + counters) stays visible while scrolling; thead stacks below via ResizeObserver-driven `--chrome-height`. Drag handles on every header th resize columns; widths persist in localStorage. Refresh interval 2s → 5s.
> - **CLI**: `--version`, `check-openmodelica`, `check-julia`, `--simulator`/`--simulator-path`/`--work-dir` added to `compare`, stale `--report` help fixed, bogus `manifest rebuild` removed from docs.
> - **Demo-lib gap closed**: ModelicaTestingLib gained 11 Linux Dymola baselines (it had only Windows Dymola + Linux OpenModelica before). All four partitions now populated.
>
> 861 unit tests pass + 3 expected skips; 0 regressions. TRANSFORM Linux Dymola: 326/326 PASS, up from prior session's 319/326 (97.85%). Working tree clean.
>
> Pending: roadmap #7 (capabilities) — Julia recognizer, MTK FMU export, Dyad validation, experiment alignment. Each is a separate session.
>
> Pre-session sanity: `git log --oneline -10`, `uv run pytest -q --ignore=tests/test_interactive_html_snapshot.py` (expect 861 passed + 3 skipped). Smoke each backend; verify `dashboard.html` exists, sticky chrome stays visible while scrolling, drag-resize on the Model column persists across the 5s meta-refresh tick.
