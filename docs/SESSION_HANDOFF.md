# Session handoff — DSTF persistent-worker abstraction + JS scorer parity floor

**Date**: 2026-04-29
**Covers**: D88 (persistent-worker refactor + bug fixes) → D89 (audit-driven debt sweep + JS scorer parity)
**State at HEAD** (commit `5d72bd6`):
- **842 tests passing + 3 expected optional-dep skips, 0 regressions** (full suite runs in ~4½ min on Linux WSL)
- **5 simulator backends, 3 test libraries** unchanged
- **All five live-preview comparison modes are now JS↔Python parity-tested** — `nrmse`, `tube`, `range`, `points`, `dominant-frequency`. The sixth (`event-timing`) stays CLI-authoritative per design (no JS scorer to drift). Test at `tests/test_scorer_parity.py`; deliberate JS mistuning fails it with a per-leaf diagnostic.
- **Persistent-worker abstraction extracted** — `Worker` ABC + `PersistentRunnerBase` template-method class in `simulators/base.py` own the dispatch loop; each persistent runner shrank by 150-180 lines (498 lines of duplicated boilerplate eliminated). Adding a new persistent backend is now a ~30-line job (set `worker_cls` + `backend_label` + override 2-3 hooks).
- **CLI runner selection is registry-driven** — zero backend-name strings in selection logic. New persistent backends plug in by overriding `persistent_runner_cls()` + `preflight()` on their runner classes; no CLI edit.
- **Capability declarations are honest at registration** — `@register` decorator validates that flagged capabilities have matching method overrides; caught one stale `FMU_EXPORT` flag (DymolaRunner batch class) and produced a `TODO(batch-fmu-export)` placeholder.
- **`comparator.py` split** into `types.py` (85 LOC dataclasses) + `algorithms.py` (1023 LOC pure compute) + `comparator.py` (428 LOC orchestration, was 1424).
- **`plot_comparison.py`'s 444-line `_build_template_context` is now a 75-line orchestrator** over 9 focused `_build_*` helpers.

**Cross-OS validation**: User confirmed Windows + Linux runs both work end-to-end this session. Linux Dymola via the `/usr/local/bin/dymola` wrapper script (the bare `bin64/dymola` binary fails because it bypasses the `LD_LIBRARY_PATH` setup the wrapper does — DEBT marker added at the worker construction site).

**TRANSFORM-on-Dymola-Linux validation (post-D90)**: 326-test suite runs at **97% pass rate** (319 pass / 4 fail / 3 timeout) with the resilience fixes from D90 in place. The 7 problem tests cluster as 3 real per-test timeouts (CIET_nureth, IRIS_Default_Teststandalone, HumTest — these models legitimately exceed their per-test budgets on this Linux Dymola) plus 4 collateral failures (the test immediately after each timeout sees a transient broken-worker state, which the health probe detects and forces a restart). Without D90's fixes the same suite produced 21/305/4 — a single timeout cascaded into 305 silent failures. The "84 license-tier failures" diagnosed during the pre-D90 lucky run were *not* actual Dymola license-capacity issues; they were the broken-worker cascade replaying stale cached error log content (the first model that genuinely hit license-tier limits left its message in Dymola's log buffer, which subsequent broken-worker `savelog` calls re-dumped). With proper worker recovery, all 84 tests translate cleanly.

---

## Session arc

Two arcs over one session. Each is a multi-commit unit on `main`:

| Arc | Range | Theme |
|---|---|---|
| **D88** | `7e68e61` → `1e1983e` (4 commits) | **Persistent-worker abstraction.** Started from a Linux OM run that surfaced three bugs in the persistent-worker code path: (BUG-A) "All workers failed to start. Aborting." was actually a `print`+`return` that let read/compare/report run on empty results producing fake "ok" outcomes; (BUG-B) Dymola wheel-extract cache used a path-only `.extracted` marker that happily reused a 32-byte stub `dymola_interface.py` even after the wheel was replaced; (BUG-C) duplicate `Running N tests...` line at `cli.py:200`. After fixing those + cleanup of dead `@register("Julia.Persistent")` + xdg-open spam suppression for headless Linux, refactored the three persistent runners into a `Worker` ABC + `PersistentRunnerBase` template (Phases 1-5) + docs. Each persistent runner shrank ~50% by line count; the dispatch logic exists once. CLI's `_get_runner` lost all backend-name strings via `persistent_runner_cls()` + `preflight()` classmethod hooks. |
| **D89** | `9af3f57` → `5d72bd6` (7 commits) | **Audit-driven debt sweep + JS scorer parity floor.** A code-review pass after D88 surfaced: (#1) `Config.simulator = "Dymola"` was a literal-as-sentinel default, causing `--simulator Dymola` from CLI to be silently overridden by testing.json — fixed via `Optional[str] = None`. (#2) capability validator at `@register` decoration time. (#3) argparse setup extracted from `main()` into `build_arg_parser()` + module-scope `_COMMANDS` table + a parity test that catches subcommand/dispatch-table drift. Then per user direction: option C JS↔Python scorer parity test (initially 4 modes, extended to 5 to include `dominant-frequency` after discovering its FFT-based JS scorer does exist despite vision.md claiming otherwise). Cross-reference markers at every Python↔JS scorer pair so a `comparator.py` change reminds the dev to update the JS too. Line-ending normalization via `.gitattributes` + one-time renormalize cleared 30 files of CRLF/LF noise. `comparator.py` split into `types.py` + `algorithms.py` + `comparator.py`. `plot_comparison.py`'s 444-line `_build_template_context` extracted into 9 focused helpers + a 75-line orchestrator. |

Total: 11 commits. (Plus one user housekeeping commit `95831d4` removing `.claude/settings.local.json` from tracking — unrelated to D88/D89 themes.)

Each arc has commit messages capturing per-step rationale; refer to `git log` for the full audit trail.

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

## Plan-quality lessons learned this session

Worth carrying forward:

1. **Read the vision/usage docs before recommending architectural alternatives.** Mid-session I proposed Pyodide / hybrid-server alternatives to the JS↔Python duplication without having read `vision.md:98-110` — which already explicitly forbids server-mode (`No local server; no live-apply; no auto-rerun`), explicitly defers JS unit tests (`unless the reporter becomes a regression source`), and pins a 5 MB embedded payload budget that Pyodide alone violates. The user caught it; I had to course-correct. Lesson: treat documented design constraints as load-bearing, not as optional context. A 30-second `grep -n "interactive\|reporter\|live-preview" docs/vision.md` would have caught it.

2. **Verify "this mirrors that" claims by searching for the counterpart, not by assuming.** I pitched extending parity testing to `MODE_PLOT_CONTRIBUTIONS` thinking it mirrored Python; turns out it has no Python counterpart at all (Plotly traces are JS-only). One `grep` for the supposed Python function would have caught it. Lesson: when proposing a parity test, the first action is `grep` for the named Python counterpart in `src/`; if it doesn't exist, the parity surface doesn't exist.

3. **Sentinel-as-default is a bug, not a style choice.** `Config.simulator = "Dymola"` looked like a reasonable default but collapsed two semantically-distinct cases ("user explicitly requested Dymola" vs "user didn't specify") into one indistinguishable string. The bug-symptom — `--simulator Dymola` getting silently overridden by testing.json — was not surfaced by any test until I added one. Future audits: any field where the default is a non-`None` literal that gets compared against in resolution logic deserves a re-think.

4. **Capability declarations need mechanical enforcement.** D86's tube taxonomy showed similar drift potential; this session's `Capability.FMU_EXPORT` on `DymolaRunner` (declared, never implemented at the batch level) shows it's a real failure mode. The `@register` validator catches it at module-import time.

5. **Line-ending hygiene needs explicit `.gitattributes` for cross-OS dev.** WSL on Windows + Windows-native tooling produced 30 CRLF working-tree files that survived multiple sessions as persistent `git status` noise. `.gitattributes` with `* text=auto eol=lf` plus a one-time renormalize fixed it. New contributors get LF in the index automatically.

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
git log --oneline -15                                         # confirms D88..D89 (HEAD = 5d72bd6)
uv run pytest -q --ignore=tests/test_interactive_html.py      # expect 842 passed + 3 skipped, 0 failures
export PATH="$HOME/.juliaup/bin:$PATH" && uv run pytest -q    # same on Julia-installed envs

# Smoke tests (each should produce PASS):
uv run dstf --config examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json run --simulator Dymola
uv run dstf --config examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json run --simulator OpenModelica
uv run dstf --config examples/julia/JuliaMtkTestingLib/Resources/ReferenceResults/testing.json run
uv run dstf --config examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json run
uv run dstf --config examples/fmu/testing.json run   # requires reference-fmus-binaries/
```

**Working tree should be clean** — the CRLF/LF noise from prior sessions is gone after this session's `.gitattributes` normalization.

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

> Resuming DSTF (Dynamic Systems Testing Framework) at commit `5d72bd6` on `main`. The previous session was a 2-arc cleanup pass (D88: persistent-worker abstraction extraction + bug fixes from a Linux OM run; D89: audit-driven debt sweep + JS scorer parity floor). Highlights:
>
> - 842 unit tests pass + 3 expected skips, 0 regressions. JS↔Python scorer parity for all 5 live-preview modes (`tests/test_scorer_parity.py`).
> - `Worker` ABC + `PersistentRunnerBase` template in `simulators/base.py`; persistent runners shrank ~50%; new persistent backends are now ~30 LOC. CLI runner selection is fully registry-driven via `persistent_runner_cls()` + `preflight()` classmethod hooks (zero backend-name strings in `cli.py:_get_runner`).
> - `comparator.py` split into `types.py` + `algorithms.py` + `comparator.py`. `plot_comparison.py`'s 444-line `_build_template_context` is now a 75-line orchestrator over 9 focused helpers.
> - User confirmed Windows + Linux runs both work end-to-end. Linux Dymola via `/usr/local/bin/dymola` wrapper; bare `bin64/dymola` fails (DEBT marker on the worker construction site).
> - Roadmap items #1-#6 + #8 closed. #7 (capabilities) still pending; A-tier next moves are characterizing Linux Dymola timeouts the user observed and validating against `TRANSFORM-UnitTests`.
>
> Pre-session sanity: `git log --oneline -15`, `uv run pytest -q --ignore=tests/test_interactive_html.py` (expect 842 passed + 3 skipped). Smoke each backend with the example testing.json files. CRLF/LF noise from prior sessions is gone.
