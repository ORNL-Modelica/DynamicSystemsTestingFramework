# Session handoff — v1.0-cleanliness sweep + dashboard/report unification

**Date**: 2026-04-30
**Covers**: three arcs over one session — modelica-testing CLI sweep + CLI subcommand parity (commits `1780121`–`ac7417b`); annotation ↔ test_spec.json contract docs (commit `b0124ad`); dashboard + report unification (commits `b6053e1`–`2a8f7da`, 11 commits including the plan doc).
**State at HEAD** (commit `2a8f7da`):
- **857 tests passing + 3 expected optional-dep skips, 0 regressions** (full suite runs in ~4¼ min on Linux WSL). +15 over the prior 842 baseline (new tests for field-source provenance, dashboard render, progress reporter).
- **5 simulator backends, 3 test libraries** unchanged.
- **One unified `dashboard.html` replaces both the live-progress page and the post-run index** — single Jinja template + `dashboard.js` module fed by `status.json` (live, JS-fetched every 2s, scroll-preserving) and per-test `comparison_data.json` sidecars (final). New **Resolution** column surfaces per-field provenance (`annotation` / `test_spec` / `mixed`) so users can answer "where did this stop_time come from?" without docs lookup.
- **`--report` flag narrows in scope** — controls only per-test `interactive.html` deep dives now. The unified dashboard always exists; `cmd_run` and `cmd_compare` always trigger `dashboard_render.render_final` after comparison.
- **Standalone `index.html` template retired** along with the inline `_DASHBOARD_TEMPLATE` f-string in `progress.py` (~125 LOC) and the `_build_rerun_prefix` dead-code path. ProgressReporter narrowed to status.json-only writes; rendering delegates through `reporting/dashboard_render.py`.
- **CLI flag-group dedup via argparse parent parsers** — `filter_parent` (`--filter` / `--package`), `compare_parent` (`--tolerance` / `--default-points`), `report_parent` (`--report` / `--report-format`). Future shared flags (e.g. eventual `--parallel` on more subcommands) cost one line each instead of N.
- **Annotation contract is now teachable** — `docs/usage.md` has a "Two-layer contract" section + kitchen-sink test_spec.json example showing every `simulation.*` field. Audit corrected the prior bullet: `_parse_experiment` already covered 5 of 6 standard fields (StopTime, Tolerance, `__Dymola_Algorithm`, NumberOfIntervals, Interval); spec_parser already handled per-test `method` overrides. Only documentation + Resolution-column visibility were missing.
- **All four user-facing `modelica-testing` strings retired** — rerun-command builder, spec-update copy, badge tooltip, internal cache path (`~/.cache/dstf/dymola-interface/`). Six interactive_*.hash goldens regenerated for the visible spec-update copy change.

**Inherited state from prior session (D88–D90, commit `5d72bd6` baseline)**:
- **All five live-preview comparison modes JS↔Python parity-tested** — `nrmse`, `tube`, `range`, `points`, `dominant-frequency`. Sixth (`event-timing`) stays CLI-authoritative. Test at `tests/test_scorer_parity.py`.
- **Persistent-worker abstraction extracted** — `Worker` ABC + `PersistentRunnerBase` template in `simulators/base.py`; persistent runners shrank ~50%; new persistent backends are ~30 LOC.
- **CLI runner selection is registry-driven** — `persistent_runner_cls()` + `preflight()` classmethod hooks. Zero backend-name strings in `cli.py:_get_runner`.
- **Capability declarations validated at `@register`** — stale flags TypeError at module-import.
- **`comparator.py` + `plot_comparison.py` split** into focused modules.

**Cross-OS validation (inherited from D88-D90)**: Windows + Linux both work end-to-end. Linux Dymola via the `/usr/local/bin/dymola` wrapper script (the bare `bin64/dymola` binary fails because it bypasses the `LD_LIBRARY_PATH` setup the wrapper does — DEBT marker at the worker construction site).

**TRANSFORM-on-Dymola-Linux validation (inherited from D90)**: 326-test suite runs at **97% pass rate** (319 pass / 4 fail / 3 timeout) with D90's resilience fixes in place. The 7 problem tests cluster as 3 real per-test timeouts (CIET_nureth, IRIS_Default_Teststandalone, HumTest — these models legitimately exceed their per-test budgets on this Linux Dymola) plus 4 collateral failures (the test immediately after each timeout sees a transient broken-worker state, which the health probe detects and forces a restart). Without D90's fixes the same suite produced 21/305/4 — a single timeout cascaded into 305 silent failures.

---

## Session arc

Three arcs over one session. Each is a multi-commit unit on `main`:

| Arc | Range | Theme |
|---|---|---|
| **v1.0-cleanliness sweep** | `1780121` → `ac7417b` (3 commits) | **Stale CLI references + flag-group dedup.** Four user-facing `modelica-testing` strings replaced with `dstf` (rerun-command builder, spec-update copy, badge tooltip, internal cache path); 6 interactive HTML goldens regenerated for the visible spec-update copy change. Three duplicated argparse flag groups extracted to `add_help=False` parent parsers — `--filter`/`--package` deduped across discover/run/compare/export, `--tolerance`/`--default-points` across run/compare, `--report-format`/`--report` across run/compare. Side effect: `dstf compare`'s `--report-format` picked up its missing help string (single source of truth means this drift can't recur). `dstf compare --parallel` exposed (parallelism infra was already present in `generate_report_suite` ThreadPoolExecutor; only the flag plumbing was missing). Two stale matplotlib docstrings updated to reflect the post-Stage-5 reality (PNGs were retired ages ago; the parallelism rationale is now Plotly JSON serialization + sidecar dump + decimation). |
| **Annotation contract docs** | `b0124ad` (1 commit) | **Two-layer contract documentation + scope correction.** Audit revealed the original priority bullet was overscoped: `_parse_experiment` already parses 5 of 6 standard fields (StopTime, Tolerance, `__Dymola_Algorithm`, NumberOfIntervals, Interval); `spec_parser` already handles `simulation.{stop_time, tolerance, method, number_of_intervals, output_interval, timeout}`; tests at `test_discovery.py:206` exercise the per-test method override end-to-end. Real gap was *documentation* + Resolution column visibility (deferred to dashboard arc). Added "Two-layer contract" section to `docs/usage.md`: annotation provides defaults, `simulation.*` block in test_spec.json overrides; user omits → annotation if present, else simulator default; user provides → it's used. Kitchen-sink test_spec.json example expanded to show every `simulation.*` field. CLAUDE.md got brief contract paragraph. **Decided not to plumb `StartTime`** through 5 backends — most tests start at t=0, per-backend cost is high; gap is documented. |
| **Dashboard + report unification** | `b6053e1` → `2a8f7da` (11 commits including the plan doc `ac1a87b`) | **One progressively-enriching page replaces two.** Followed a written plan at `docs/superpowers/plans/2026-04-30-dashboard-report-unification.md` (TDD-shaped, 11 tasks across 7 phases). Built bottom-up: TestModel `field_sources` provenance plumbing (Task 1) → new `dashboard_render.py` module reading `status.json` + per-test `comparison_data.json` sidecars (Task 2) → rich Jinja template with sortable headers, status-button + per-column text filter (Task 3) → vanilla JS module with 3-state sort cycle (desc-first numeric, asc-first text), `setInterval(fetch('status.json'))` every 2s with DOM-only updates preserving scroll, "Refresh now" button (Task 4) → ProgressReporter delegates to dashboard_render, `_DASHBOARD_TEMPLATE` f-string deleted (Task 5) → per-test sidecar `summary` block plumbing (Task 6) → CLI always calls `render_final` after comparison regardless of `--report` (Task 7) → standalone `index.html` template retired plus `_build_rerun_prefix` dead code (Task 8) → Resolution column wired end-to-end through TestStatus + register() callsites + template + dashboard_render (Task 9) → full validation gauntlet 857/3/0 + smoke against ModelicaTestingLib OpenModelica (Task 10) → docs (Task 11). Net production-code LOC roughly flat (~+30) — new template/JS/render module additions balanced by f-string + index.html deletions. |

Total: 16 commits across the three arcs. Each arc's commits have per-step rationale messages; refer to `git log` for the full audit trail.

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
git log --oneline -20                                         # HEAD = 2a8f7da (this session); back through 5d72bd6 (prior baseline)
uv run pytest -q --ignore=tests/test_interactive_html.py      # expect 857 passed + 3 skipped, 0 failures
export PATH="$HOME/.juliaup/bin:$PATH" && uv run pytest -q    # same on Julia-installed envs

# Smoke tests (each should produce PASS + a unified dashboard.html in work_dir):
uv run dstf --config examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json run --simulator Dymola
uv run dstf --config examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json run --simulator OpenModelica
uv run dstf --config examples/julia/JuliaMtkTestingLib/Resources/ReferenceResults/testing.json run
uv run dstf --config examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json run
uv run dstf --config examples/fmu/testing.json run   # requires reference-fmus-binaries/
```

**Working tree should be clean.** The dashboard now sits at `<work_dir>/dashboard.html`; `--report` adds per-test deep dives at `<work_dir>/reports/<test>/interactive.html`.

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

* **Unify dashboard + report into one progressively-enriching page** ✅ landed in commits `b6053e1` → `315c311` (9 commits). One Jinja `dashboard.html` + `dashboard.js` module replaces the inline `_DASHBOARD_TEMPLATE` f-string in `progress.py` and the standalone `index.html` template. JS-fetch every 2s (preserves scroll), 3-state sort, per-column text filter, status-button filter, "Refresh now" button. New **Resolution** column surfaces per-field provenance (`annotation` / `test_spec` / `mixed`) — closes the resolution-explainer sub-item from the annotation contract grilling. 857 tests pass, 0 regressions. See `docs/superpowers/plans/2026-04-30-dashboard-report-unification.md` for the full plan.

* **Audit + replace stale `modelica-testing` CLI references** ✅ landed in commits `1780121` + `6696b93` + `ac7417b`. Four user-facing strings replaced; `~/.cache/modelica-testing/` → `~/.cache/dstf/`; argparse parent parsers extracted (`filter_parent`, `compare_parent`, `report_parent`); `dstf compare` got `--parallel` (parallelism infra was already present in `generate_report_suite`); two stale matplotlib docstrings updated to reflect post-Stage-5 reality.

* **Tighten the annotation ↔ test_spec.json contract** — partially landed; remainder is dashboard-bound. **Audit corrected the original bullet**: sub-items 1 + 4 were *already done in tree* (`_parse_experiment` already parses StopTime, Tolerance, `__Dymola_Algorithm`, NumberOfIntervals, Interval; `simulation.method` already overrides on per-test basis — tests at `test_discovery.py:206` exercise it end-to-end). The honest remaining scope: (a) **document the two-layer contract** in `docs/usage.md` + `CLAUDE.md` ✅ landed, (b) **expand the kitchen-sink example** in `docs/usage.md` to show every `simulation.*` field ✅ landed, (c) **resolution-explainer** — a column in the unified dashboard (or `--explain` flag) showing provenance per field (`stop_time: 10 (annotation)` vs `stop_time: 10 (test_spec)`) — defer to the dashboard unification arc since it lives there. **Decided not to plumb `StartTime`** through 5 backends — most tests start at t=0, the per-backend cost is high, and the gap is now documented. Grilling outcome stands: do NOT extend `Components.UnitTests` with simulation parameters; do NOT add per-field booleans or `null`-as-sentinel.

**A-tier (concrete user need this session)**

* **Investigate Linux Dymola timeouts.** User ran `--accept` on Linux this session and saw "several timeouts and failures." The framework abstraction held up; the actual test data is the question. Worth running with `--report` (no `--accept`) to characterize what failed visually: solver-config issues, missing dependencies, real timeouts, or platform-specific solver behavior. Establish proper `Dymola/linux/` baselines once the failing tests are understood.

* **Validate against `TRANSFORM-UnitTests`** as the strongest regression signal. The persistent-worker / CLI changes in D88+D89 touched the call paths every external user hits. Running against a real downstream library with custom recognizers + dependencies + baselines is the strongest "did we regress anything" check available without a Windows host.

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

> Resuming DSTF (Dynamic Systems Testing Framework) at commit `2a8f7da` on `main`. The previous session was a 3-arc v1.0-cleanliness + UX pass:
>
> - **857 unit tests pass + 3 expected skips, 0 regressions.** Inherited JS↔Python scorer parity infrastructure from D88-D89 stays green.
> - **One unified `dashboard.html`** replaces both the old f-string live-progress page and the standalone `index.html`. Single Jinja template + `dashboard.js` module, fed by `status.json` (live, JS-fetched every 2s, scroll-preserving) and per-test `comparison_data.json` sidecars (final). Status-button + per-column text filter, 3-state sort cycle (desc-first numeric, asc-first text), "Refresh now" button. **Resolution column** shows per-field provenance (`annotation` / `test_spec` / `mixed`).
> - **`--report` semantics narrowed** — it controls only per-test interactive deep dives now. The unified dashboard always exists; `cmd_run` and `cmd_compare` both call `dashboard_render.render_final` after comparison regardless.
> - **CLI flag-group dedup via argparse parent parsers** (filter / compare / report). `dstf compare --parallel` exposed (infra was already there).
> - **Annotation contract documented** in `docs/usage.md` (two-layer: annotation defaults, `simulation.*` overrides). Audit found `_parse_experiment` + `spec_parser` already cover everything except `StartTime`, which we deliberately did not plumb (low value, high per-backend cost; documented as a known gap).
> - **All four user-facing `modelica-testing` strings retired** plus the `~/.cache/modelica-testing/` cache path (renamed to `~/.cache/dstf/`).
>
> Roadmap items #1-#6 + #8 still closed. #7 (capabilities — Dyad, Julia recognizer, MTK FMU export, experiment alignment) still pending. **A-tier next moves**: characterizing Linux Dymola timeouts (now with the unified dashboard's NRMSE / wall-time sortable columns making triage easier) and validating against `TRANSFORM-UnitTests` as the strongest regression signal for the dashboard cutover.
>
> Pre-session sanity: `git log --oneline -20`, `uv run pytest -q --ignore=tests/test_interactive_html.py` (expect 857 passed + 3 skipped). Smoke each backend; verify the unified dashboard.html exists at `<work_dir>/dashboard.html`.
