# Session handoff — v1.0-cleanliness sweep + dashboard/report unification

**Date**: 2026-04-30
**Covers**: four arcs over one session — modelica-testing CLI sweep + CLI subcommand parity (commits `1780121`–`ac7417b`); annotation ↔ test_spec.json contract docs (commit `b0124ad`); dashboard + report unification (commits `b6053e1`–`2a8f7da`, 11 commits including the plan doc); **dashboard UX polish from two rounds of user feedback** (commits `aede963`–`334b060`, 7 commits).
**State at HEAD** (commit `334b060`):
- **860 tests passing + 3 expected optional-dep skips, 0 regressions** (full suite runs in ~4½ min on Linux WSL). +18 over the prior 842 baseline.
- **5 simulator backends, 3 test libraries** unchanged.
- **One unified `dashboard.html` replaces both the live-progress page and the post-run index** — single Jinja template + `dashboard.js` module fed by `status.json` (live, JS-fetched every 2s, scroll-preserving) and per-test `comparison_data.json` sidecars (final). New **Resolution** column surfaces per-field provenance (`annotation` / `test_spec` / `mixed`) so users can answer "where did this stop_time come from?" without docs lookup.
- **`--report` flag narrows in scope** — controls only per-test `interactive.html` deep dives now. The unified dashboard always exists; `cmd_run` and `cmd_compare` always trigger `dashboard_render.render_final` after comparison.
- **Standalone `index.html` template retired** along with the inline `_DASHBOARD_TEMPLATE` f-string in `progress.py` (~125 LOC) and the `_build_rerun_prefix` dead-code path. ProgressReporter narrowed to status.json-only writes; rendering delegates through `reporting/dashboard_render.py`.
- **CLI flag-group dedup via argparse parent parsers** — `filter_parent` (`--filter` / `--package`), `compare_parent` (`--tolerance` / `--default-points`), `report_parent` (`--report` / `--report-format`). Future shared flags (e.g. eventual `--parallel` on more subcommands) cost one line each instead of N.
- **Annotation contract is now teachable** — `docs/usage.md` has a "Two-layer contract" section + kitchen-sink test_spec.json example showing every `simulation.*` field. Audit corrected the prior bullet: `_parse_experiment` already covered 5 of 6 standard fields (StopTime, Tolerance, `__Dymola_Algorithm`, NumberOfIntervals, Interval); spec_parser already handled per-test `method` overrides. Only documentation + Resolution-column visibility were missing.
- **All four user-facing `modelica-testing` strings retired** — rerun-command builder, spec-update copy, badge tooltip, internal cache path (`~/.cache/dstf/dymola-interface/`). Six interactive_*.hash goldens regenerated for the visible spec-update copy change.
- **Dashboard battle-tested via two rounds of user feedback (Windows + Linux)** — colored status badges, fixed filter-button vocab mismatch, restored Reference + Detail columns, meta-refresh fallback (replaces non-functional JS-fetch on file:// URLs), 3-state sort cycle restart, auto-open at start of run. Then: Reference column hyperlinks to ref JSON, glob filter support, multi-toggle pills, sim_failed/no_ref/warnings counter pills, pills-as-toggles (filter-bar row deleted), per-row checkbox column + sticky footer with Copy-rerun-command + Download-selected-txt, and **localStorage persistence so filter / selection / sort survive the 2s meta-refresh ticks** (caught when an active "running" filter got wiped on each tick mid-run).

**Inherited state from prior session (D88–D90, commit `5d72bd6` baseline)**:
- **All five live-preview comparison modes JS↔Python parity-tested** — `nrmse`, `tube`, `range`, `points`, `dominant-frequency`. Sixth (`event-timing`) stays CLI-authoritative. Test at `tests/test_scorer_parity.py`.
- **Persistent-worker abstraction extracted** — `Worker` ABC + `PersistentRunnerBase` template in `simulators/base.py`; persistent runners shrank ~50%; new persistent backends are ~30 LOC.
- **CLI runner selection is registry-driven** — `persistent_runner_cls()` + `preflight()` classmethod hooks. Zero backend-name strings in `cli.py:_get_runner`.
- **Capability declarations validated at `@register`** — stale flags TypeError at module-import.
- **`comparator.py` + `plot_comparison.py` split** into focused modules.

**Cross-OS validation (this session)**: TRANSFORM-UnitTests verified on Windows side end-to-end against current HEAD — no regressions in the v1.0-cleanliness sweep, dashboard unification, or the round-3/round-4 polish. Linux side: Dymola, OpenModelica (--batch), Julia/MTK, Python all smoke-tested locally against ModelicaTestingLib via the unified dashboard. OpenModelica persistent + FMPy not exercised here (OMPython + fmpy not installed on this WSL host).

**Cross-OS validation (inherited from D88-D90)**: Windows + Linux both work end-to-end. Linux Dymola via the `/usr/local/bin/dymola` wrapper script (the bare `bin64/dymola` binary fails because it bypasses the `LD_LIBRARY_PATH` setup the wrapper does — DEBT marker at the worker construction site).

**TRANSFORM-on-Dymola-Linux validation (inherited from D90)**: 326-test suite runs at **97% pass rate** (319 pass / 4 fail / 3 timeout) with D90's resilience fixes in place. The 7 problem tests cluster as 3 real per-test timeouts (CIET_nureth, IRIS_Default_Teststandalone, HumTest — these models legitimately exceed their per-test budgets on this Linux Dymola) plus 4 collateral failures (the test immediately after each timeout sees a transient broken-worker state, which the health probe detects and forces a restart). Without D90's fixes the same suite produced 21/305/4 — a single timeout cascaded into 305 silent failures.

---

## Session arc

Four arcs over one session. Each is a multi-commit unit on `main`:

| Arc | Range | Theme |
|---|---|---|
| **v1.0-cleanliness sweep** | `1780121` → `ac7417b` (3 commits) | **Stale CLI references + flag-group dedup.** Four user-facing `modelica-testing` strings replaced with `dstf` (rerun-command builder, spec-update copy, badge tooltip, internal cache path); 6 interactive HTML goldens regenerated for the visible spec-update copy change. Three duplicated argparse flag groups extracted to `add_help=False` parent parsers — `--filter`/`--package` deduped across discover/run/compare/export, `--tolerance`/`--default-points` across run/compare, `--report-format`/`--report` across run/compare. Side effect: `dstf compare`'s `--report-format` picked up its missing help string (single source of truth means this drift can't recur). `dstf compare --parallel` exposed (parallelism infra was already present in `generate_report_suite` ThreadPoolExecutor; only the flag plumbing was missing). Two stale matplotlib docstrings updated to reflect the post-Stage-5 reality (PNGs were retired ages ago; the parallelism rationale is now Plotly JSON serialization + sidecar dump + decimation). |
| **Annotation contract docs** | `b0124ad` (1 commit) | **Two-layer contract documentation + scope correction.** Audit revealed the original priority bullet was overscoped: `_parse_experiment` already parses 5 of 6 standard fields (StopTime, Tolerance, `__Dymola_Algorithm`, NumberOfIntervals, Interval); `spec_parser` already handles `simulation.{stop_time, tolerance, method, number_of_intervals, output_interval, timeout}`; tests at `test_discovery.py:206` exercise the per-test method override end-to-end. Real gap was *documentation* + Resolution column visibility (deferred to dashboard arc). Added "Two-layer contract" section to `docs/usage.md`: annotation provides defaults, `simulation.*` block in test_spec.json overrides; user omits → annotation if present, else simulator default; user provides → it's used. Kitchen-sink test_spec.json example expanded to show every `simulation.*` field. CLAUDE.md got brief contract paragraph. **Decided not to plumb `StartTime`** through 5 backends — most tests start at t=0, per-backend cost is high; gap is documented. |
| **Dashboard + report unification** | `b6053e1` → `2a8f7da` (11 commits including the plan doc `ac1a87b`) | **One progressively-enriching page replaces two.** Followed a written plan at `docs/superpowers/plans/2026-04-30-dashboard-report-unification.md` (TDD-shaped, 11 tasks across 7 phases). Built bottom-up: TestModel `field_sources` provenance plumbing (Task 1) → new `dashboard_render.py` module reading `status.json` + per-test `comparison_data.json` sidecars (Task 2) → rich Jinja template with sortable headers, status-button + per-column text filter (Task 3) → vanilla JS module with 3-state sort cycle (desc-first numeric, asc-first text), `setInterval(fetch('status.json'))` every 2s with DOM-only updates preserving scroll, "Refresh now" button (Task 4) → ProgressReporter delegates to dashboard_render, `_DASHBOARD_TEMPLATE` f-string deleted (Task 5) → per-test sidecar `summary` block plumbing (Task 6) → CLI always calls `render_final` after comparison regardless of `--report` (Task 7) → standalone `index.html` template retired plus `_build_rerun_prefix` dead code (Task 8) → Resolution column wired end-to-end through TestStatus + register() callsites + template + dashboard_render (Task 9) → full validation gauntlet 857/3/0 + smoke against ModelicaTestingLib OpenModelica (Task 10) → docs (Task 11). Net production-code LOC roughly flat (~+30) — new template/JS/render module additions balanced by f-string + index.html deletions. |
| **Dashboard UX polish (2 rounds of feedback)** | `aede963` → `334b060` (7 commits) | **Caught real bugs the plan didn't anticipate, plus design refinements.** Round 1 fixes after Windows verification: status_class vocab mismatch (live `passed`/`failed` vs filter `pass`/`fail` — buttons did nothing) → introduced `_LIVE_STATUS_MAP` to normalize; status badges were plain text → added colored `.status-badge` CSS; lost `Ref` and `Detail` columns from the old templates → restored both; counter pills were uncolored → added per-status border + value colors; meta-refresh fallback (JS fetch silently blocked on `file://` URLs in Chrome/Edge — auto-refresh + Refresh-now button were both no-ops in practice) → reverted to `<meta http-equiv="refresh">`; sort cycle stuck at null after 3 clicks → added cycle restart at firstClick; auto-open dashboard at start of `cmd_run` (not end). **Round 2** refinements: Reference column renamed and hyperlinked to ref JSON (file:// URL via `Path.as_uri()`); per-column filter input overflow → CSS `box-sizing: border-box`; new `SIM_FAILED` / `NO_REF` / `WARNINGS` counter pills; **counter pills became dual-purpose filter toggles** (the separate filter-bar row deleted entirely — one UI element does the work of two); per-column text filter gained glob support (`*`/`?` → regex; substring otherwise); status pills became multi-toggle (Failed + Sim-Failed simultaneously, Total clears all); restored the **rerun helpers** in a sticky footer (per-row checkboxes, tristate header select-all-visible, Download `selected.txt`, Copy rerun command, live preview, hidden-by-filter hint) — design confirmed via grill (filters and selection fully decoupled, persisted-basket model). **Final polish**: `localStorage` persistence so filter/selection/sort survive the 2s meta-refresh ticks (caught when an active "running" filter got wiped on each tick mid-run). |

Total: 22 commits across the four arcs. Each arc's commits have per-step rationale messages; refer to `git log` for the full audit trail.

---

## Dev env (unchanged from previous handoff)

```bash
# One-time setup
uv pip install -e ".[dev]"           # adds scipy now (Python e2e tests need it)
uv pip install pytest-playwright     # JS↔Python parity tests need this
uv run playwright install chromium   # ~120 MB; first time only
```

scipy moved into `[dev]` extras this session — was previously implicit on miniforge's pytest, but `uv run pytest` on a clean `.venv` would fail the `_scipy_available` subprocess check. New users can now do the standard `uv pip install -e ".[dev]"` and get the full suite running.

---

## Backends (5, all production)

Unchanged from prior session — but the persistent-worker plumbing under each is significantly cleaner now:

| Backend | Class | Persistent runner | Worker class | Capability flags |
|---|---|---|---|---|
| **Dymola** | `DymolaRunner` | `PersistentDymolaRunner` | `DymolaWorker` | PERSISTENT_WORKERS, BATCH_FALLBACK, FMU_EXPORT |
| **OpenModelica** | `OpenModelicaRunner` | `PersistentOpenModelicaRunner` | `OpenModelicaWorker` | PERSISTENT_WORKERS, BATCH_FALLBACK |
| **Julia/MTK** | `JuliaRunner` | `PersistentJuliaRunner` | `JuliaWorker` | PERSISTENT_WORKERS, BATCH_FALLBACK |
| **FMPy** | `FmpyRunner` | — | — | PERSISTENT_WORKERS |
| **Python** | `PythonRunner` | — | — | BATCH_FALLBACK |

The persistent runners all subclass `PersistentRunnerBase` (in `simulators/base.py`); their workers subclass `Worker` (same module). The orchestration template owns startup/dispatch/teardown; backends fill in `worker_cls`, `backend_label`, `make_worker`, optional `setup_before_workers`, `preflight`, `starting_workers_message`. See `docs/extensibility.md` §3 "Persistent-worker contract" — the canonical worked example is `PersistentOpenModelicaRunner` (smallest of the three).

Capability honesty validator at `simulators/__init__.py:_validate_capabilities` enforces that declared flags have matching method overrides. Stale flags fail at module-import time (TypeError) instead of later `NotImplementedError`.

---

## Reporter-as-IDE — feature complete + parity-tested

Status unchanged from prior session, but the **JS scorer drift surface is now mechanically tested**:

* `tests/test_scorer_parity.py` — 1 playwright test, 10 fixtures (5 modes × 2 verdicts each). Renders synthetic ref/act trajectories into `interactive.html`, runs the actual `_compare_*` Python functions for the authoritative verdict, evaluates `MODE_SCORERS[mode](leaf)` from JS via `page.evaluate()`, asserts every leaf agrees. Disagreements are reported per-leaf so a single failure surfaces full-extent drift.
* Cross-reference markers at every Python↔JS scorer pair: `comparator.py` `_compare_*` sites point at the JS line; `interactive.js` `MODE_SCORERS` entries point at the Python function. `git grep parity-test` finds them all.
* vision.md "Live preview policy" updated — D75-D76 added the FFT scorer; the doc had said `dominant-frequency` was CLI-authoritative, but JS has had a live FFT scorer since then. Doc now reflects reality.

Drift-detection has teeth: deliberately mistuning the JS NRMSE scorer (`nrmse < tol * 0.001`) makes the test fail with `"nrmse (pass): Python=True, JS=False — params={'tolerance': 0.01}"`. Same for `dominant-frequency`. Restoring the JS makes it pass.

---

## Plan-quality lessons (carried forward from prior sessions + this session)

Worth carrying forward:

1. **Audit before scoping.** This session corrected two grilling-derived priority bullets after audit: the annotation-contract bullet claimed `_parse_experiment` only handled StopTime+Tolerance (false — covered 5 of 6 standard fields already); the `--parallel`-on-compare bullet implied threading work (false — `generate_report_suite` already had ThreadPoolExecutor; only the flag plumbing was missing). Lesson: when a priority is presented as "implement X," the first move is a `grep` + targeted read to confirm X doesn't exist. Two arcs in this session collapsed from "implement" to "document" or "plumb one line" once the audit landed.

2. **Read the vision/usage docs before recommending architectural alternatives.** From D88-D89: pitched Pyodide / hybrid-server alternatives without reading `vision.md:98-110` which explicitly forbids server-mode and pins a 5 MB embedded payload budget. Treat documented design constraints as load-bearing.

3. **Verify "this mirrors that" claims by searching for the counterpart.** From D88-D89: pitched extending parity testing to `MODE_PLOT_CONTRIBUTIONS` thinking it mirrored Python; turns out no Python counterpart exists. First action when proposing a parity test: `grep` for the named Python counterpart.

4. **Sentinel-as-default is a bug, not a style choice.** From D88-D89: `Config.simulator = "Dymola"` collapsed two semantically-distinct cases. Future audits: any field where the default is a non-`None` literal that gets compared against in resolution logic deserves a re-think.

5. **Capability declarations need mechanical enforcement.** From D88-D89: `Capability.FMU_EXPORT` on `DymolaRunner` was declared but unimplemented at the batch level. The `@register` validator catches it at module-import time.

6. **TDD-shaped subagent dispatch works for plan execution.** This session's dashboard-unification arc dispatched 11 fresh subagents through `docs/superpowers/plans/2026-04-30-dashboard-report-unification.md`. Each task: failing test, minimal implementation, green test, commit. Two real bugs caught by subagents during execution that the plan didn't anticipate: (a) Jinja recursion on a literal `{% include %}` in a JS comment when the JS itself was rendered as a Jinja template; (b) JS cell-index drift when a new column was inserted, breaking live-mode polling. Both fixed in-flight. Lesson: subagent autonomy on TDD-shaped tasks catches bugs the planner won't see, but the parent agent must read the diff (not just the agent's summary) to verify what landed.

---

## Known limitations (deferred by design)

Updated from prior session:

| Item | Why | Workaround |
|---|---|---|
| Event-timing live JS scorer | Event-pairing algorithm non-trivial; CLI authoritative | Pill shows CLI result until next CLI rerun |
| **Dymola batch FMU export** | `translateModelFMU` works in persistent mode (via `DymolaInterface`); the `.mos`-driven batch path is declared as a capability but not yet wired. `TODO(batch-fmu-export)` marker in `DymolaRunner.export_fmu`. | Use persistent mode (drop `--batch`) for cross-backend chains; or contribute the `.mos`-script implementation (~30-50 LOC) |
| **`_find_top_n_peaks` JS↔Python parity** | Used for "Detect peaks from reference" UX in dominant-frequency authoring. Drift = different default peak suggestions in UI vs CLI. **Doesn't affect actual test verdicts.** Authoring-UX correctness only. | Skip until a real drift case appears |
| **`renderModeControlsHtmlJs` parity** | HTML-string parity testing is brittle (whitespace, attribute order). Python schema is the source of truth; JS is "a dumb walker" per its own comment. | Trust the Python golden-file snapshots |
| Tube per-point-per-side width modes | JS UI stores; polygon uses global mode | Use synced mode |
| Window brush one-shot per activation | UX choice | Click brush again to redo |
| Multi-select wrap in tree editor | Deferred | Wrap single, then move siblings |
| JuliaRunner FMU export | `MTK.generate_fmu` not wired | Run directly via Julia runner |
| Persistent-worker Python | D77→D78 progression not yet applied | Subprocess-per-test sufficient for typical suites |
| Dyad tests | Untested (should work — compiles to MTK) | Port a sample when concrete need arises |
| Bug 3 reproduction (Plotly autorange-stuck) | Doesn't reproduce via state-mutation path | Characterization test guards current behavior |
| Points mode live edge mirror during drag | Plotly doesn't fire `plotly_relayouting` for shape edits | Snap-on-release accepted |
| ModelicaTestingLib EventTest / IntervalTest / NoUnitTest / SimulateOnlyTest on Julia | Deferred | — |
| **`Dymola/linux/` baselines empty** | User accepted Linux Dymola baselines this session; some timed out / failed. The framework runs but the regression set hasn't fully landed. | Investigate timeouts on the failing tests; re-run with longer `--timeout` or skip-list the chronically slow ones |

---

## Pre-session sanity

```bash
git log --oneline -25                                         # HEAD = 334b060; back through 5d72bd6 (prior baseline)
uv run pytest -q --ignore=tests/test_interactive_html.py      # expect 860 passed + 3 skipped, 0 failures
export PATH="$HOME/.juliaup/bin:$PATH" && uv run pytest -q    # same on Julia-installed envs

# Smoke tests (each should produce PASS + a unified dashboard.html in work_dir
# that auto-opens in browser at the start of the run):
uv run dstf --config examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json run --simulator Dymola
uv run dstf --config examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json run --simulator OpenModelica
uv run dstf --config examples/julia/JuliaMtkTestingLib/Resources/ReferenceResults/testing.json run
uv run dstf --config examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json run
uv run dstf --config examples/fmu/testing.json run   # requires reference-fmus-binaries/
```

**Working tree should be clean.** The dashboard sits at `<work_dir>/dashboard.html`; `--report` adds per-test deep dives at `<work_dir>/reports/<test>/interactive.html`. Filter / selection / sort state is persisted to `localStorage` keyed by file path, so the page survives the 2s meta-refresh ticks during a live run without losing UI state.

---

## Candidate next moves

User's roadmap (`#1`-`#8` from the D80-D87 session) is intact; this session was orthogonal cleanup. Status:

| # | Item | Status |
|---|---|---|
| 1 | DSTF rename | ✓ D81 |
| 2 | Range metric bugs + cross-metric standardization | ✓ Range-fix arc |
| 3 | Event-timing HTML editor | ✓ D82 |
| 4 | Final-only → point-based | ✓ D84 |
| 5 | Baseline-free NO_REF short-circuit | ✓ D83 |
| 6 | Code review / tech-debt review | ✓ D86 + D87 + this session (D88+D89) |
| 7 | New capabilities (experiment alignment, Dyad, Julia recognizer, FMU export matrix) | **pending** |
| 8 | Docs cleanup throughout | ✓ commit `397c4d6` |

### Next session — top-of-stack candidates

**User-requested next-session priorities** (confirmed via grill 2026-04-30)

* **Unify dashboard + report into one progressively-enriching page** ✅ landed in commits `b6053e1` → `334b060` (18 commits including the plan doc, the Task-1-through-Task-11 implementation, and 7 follow-up polish commits from two rounds of Windows-verification feedback). One Jinja `dashboard.html` + `dashboard.js` module replaces the inline `_DASHBOARD_TEMPLATE` f-string in `progress.py` and the standalone `index.html` template. **Live mode**: meta-refresh every 2s (JS-fetch was tried first but is silently blocked on `file://` URLs in Chrome/Edge); auto-opens at start of `cmd_run`; localStorage persists filter/selection/sort across the refresh ticks. **Filtering**: counter pills are dual-purpose (informational + clickable filter toggles, multi-select; Total clears all); pills with 0 count are disabled; per-column text filter with glob support (`*`/`?`); 3-state sort cycle (desc-first numeric, asc-first text). **Selection + rerun**: per-row checkboxes + tristate select-all-visible header; sticky footer hidden when 0 selected, contains Download `selected.txt` + Copy-rerun-command (auto-switches to `--filter @selected.txt` form for >3 selections); selection persists across filter changes. **Resolution column**: per-field provenance (`annotation` / `test_spec` / `mixed`) — closes the resolution-explainer sub-item. **Reference column**: hyperlinks to the underlying ref JSON via `file://` URL. 860 tests pass, 0 regressions. See `docs/superpowers/plans/2026-04-30-dashboard-report-unification.md` for the original plan.

* **Audit + replace stale `modelica-testing` CLI references** ✅ landed in commits `1780121` + `6696b93` + `ac7417b`. Four user-facing strings replaced; `~/.cache/modelica-testing/` → `~/.cache/dstf/`; argparse parent parsers extracted (`filter_parent`, `compare_parent`, `report_parent`); `dstf compare` got `--parallel` (parallelism infra was already present in `generate_report_suite`); two stale matplotlib docstrings updated to reflect post-Stage-5 reality.

* **Tighten the annotation ↔ test_spec.json contract** — partially landed; remainder is dashboard-bound. **Audit corrected the original bullet**: sub-items 1 + 4 were *already done in tree* (`_parse_experiment` already parses StopTime, Tolerance, `__Dymola_Algorithm`, NumberOfIntervals, Interval; `simulation.method` already overrides on per-test basis — tests at `test_discovery.py:206` exercise it end-to-end). The honest remaining scope: (a) **document the two-layer contract** in `docs/usage.md` + `CLAUDE.md` ✅ landed, (b) **expand the kitchen-sink example** in `docs/usage.md` to show every `simulation.*` field ✅ landed, (c) **resolution-explainer** — a column in the unified dashboard (or `--explain` flag) showing provenance per field (`stop_time: 10 (annotation)` vs `stop_time: 10 (test_spec)`) — defer to the dashboard unification arc since it lives there. **Decided not to plumb `StartTime`** through 5 backends — most tests start at t=0, the per-backend cost is high, and the gap is now documented. Grilling outcome stands: do NOT extend `Components.UnitTests` with simulation parameters; do NOT add per-field booleans or `null`-as-sentinel.

**A-tier (concrete user need this session)**

* **Validate against `TRANSFORM-UnitTests`** ✅ confirmed by user on Windows side at HEAD `334b060`. No regressions observed in the v1.0-cleanliness sweep, dashboard unification, or polish rounds. The dashboard's new sortable/filterable UI + Copy-rerun-command made triage faster than the prior multi-page workflow.

* **Investigate Linux Dymola timeouts.** Still pending. User previously ran `--accept` on Linux Dymola and saw "several timeouts and failures." The framework abstraction held up; the actual test data is the question. With the new dashboard's NRMSE / wall-time sortable columns + Failed/Timed-out filter pills, triage is much easier than before. Worth running with `--report` (no `--accept`) to characterize what failed: solver-config issues, missing dependencies, real timeouts, or platform-specific solver behavior. Establish proper `Dymola/linux/` baselines once the failing tests are understood.

**B-tier (recommended starts for #7 capabilities)**

Same list as D87 handoff — none of these were touched this session:

* **Experiment-data alignment preprocessing** (ideas.md #57). ~3-5 days. Needs a concrete user use case.
* **MTK FMU export** via `ModelingToolkit.generate_fmu` (~1 day). Wires Julia into the `Capability.FMU_EXPORT` cross-backend chain.
* **Dyad validation** (~½ day). Port one Dyad sample.
* **Julia source recognizer** (~1 day). Auto-discover Julia tests from `.jl` files.

**Smaller follow-ups (C / D-tier)**

* **`Dymola/linux/` baselines** — couples to A-tier item above; once timeouts are understood, accept.
* **CLI subcommand parity + standardization** (ideas.md #62). Concrete known gap: `dstf compare` has no `--parallel` and runs serially even when the user has cores. Wider audit warranted across all subcommands. Hard constraint: same-or-reduction LOC; the right mechanism is argparse parent parsers (`parents=[...]`) so flag groups dedupe across subcommands. ~½ day, net-negative LOC.
* **Dymola batch FMU export** (`TODO(batch-fmu-export)`). ~30-50 LOC `.mos` script work; deferred until a batch-only codebase actually needs it.
* **Persistent-worker Python** (ideas.md #58). Mirrors Julia D77→D78. Defer until perf ceiling hits.
* **Live edge mirror for box-resize drag** (D85 follow-up). Snap-on-release UX is acceptable today; revisit if any user actually misses live mirror.
* **`check-openmodelica` / `check-julia` CLI subcommands**. Symmetric with `check-dymola`.
* **OM FMU export** via `buildModelFMU`.
* **`_find_top_n_peaks` JS parity** (~30 min) — closes the last scoring-related JS↔Python drift surface, but only matters for the "Detect peaks from reference" UX feature.
* **`reference_store.py` review** (~1-2 hr) — 933 lines but coordinated; less clear there's a clean seam without first reading carefully.
* **Visual-regression Playwright screenshots**.
* **Phase 9 dataset types** (E-tier foundational): Events / Spectrum / Distribution / Scalars / Field.

---

## Starter prompt for the next session

> Resuming DSTF (Dynamic Systems Testing Framework) at commit `334b060` on `main`. The previous session was a 4-arc v1.0-cleanliness + dashboard-unification + 2-rounds-of-polish pass:
>
> - **860 unit tests pass + 3 expected skips, 0 regressions.** Inherited JS↔Python scorer parity infrastructure from D88-D89 stays green.
> - **One unified `dashboard.html`** replaces both the old f-string live-progress page and the standalone `index.html`. Single Jinja template + `dashboard.js` module fed by `status.json` (live, meta-refresh every 2s) and per-test `comparison_data.json` sidecars (final). **localStorage** persists filter/selection/sort across the refresh ticks. **Counter pills are dual-purpose** (informational + clickable filter toggles, multi-select; Total clears all). **Per-column text filter** with substring or glob (`*`/`?`). **3-state sort cycle** (desc-first numeric, asc-first text). **Per-row checkboxes** + tristate select-all-visible header + sticky footer with Download `selected.txt` + Copy-rerun-command (auto-switches to `--filter @selected.txt` for >3 selections). Selection persists across filter changes. **Resolution column** shows per-field provenance. **Reference column** hyperlinks to ref JSON.
> - **`--report` semantics narrowed** — controls only per-test interactive deep dives now. The unified dashboard always exists; auto-opens at start of `cmd_run`; `cmd_run` and `cmd_compare` both call `dashboard_render.render_final` after comparison regardless of `--report`.
> - **CLI flag-group dedup via argparse parent parsers** (filter / compare / report). `dstf compare --parallel` exposed.
> - **Annotation contract documented** in `docs/usage.md` (two-layer: annotation defaults, `simulation.*` overrides). `StartTime` deliberately not plumbed (documented gap).
> - **All four user-facing `modelica-testing` strings retired** plus the `~/.cache/modelica-testing/` cache path (renamed to `~/.cache/dstf/`).
> - **TRANSFORM-UnitTests verified on Windows** at HEAD — no regressions.
>
> Roadmap items #1-#6 + #8 still closed. #7 (capabilities — Dyad, Julia recognizer, MTK FMU export, experiment alignment) still pending. **A-tier next move**: characterizing Linux Dymola timeouts (the dashboard's new NRMSE / wall-time sortable columns + Failed/Timed-out filter pills make triage much easier than before).
>
> Pre-session sanity: `git log --oneline -25`, `uv run pytest -q --ignore=tests/test_interactive_html.py` (expect 860 passed + 3 skipped). Smoke each backend; verify the dashboard.html exists at `<work_dir>/dashboard.html` and auto-opens in the browser.
