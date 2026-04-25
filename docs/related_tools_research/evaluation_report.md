# Evaluation Report: ModelicaTesting (now DSTF)

## Executive Summary

> Note: This evaluation was written when the tool was named ModelicaTesting. It has since been renamed to **Dynamic Systems Testing Framework (DSTF)** (see D81). The body below is preserved as-written for historical accuracy.

ModelicaTesting is a **Dymola-native, interactive regression harness for Modelica libraries** — roughly 7,900 LoC Python with a 2,400-LoC test suite. It is substantially more sophisticated than a prototype: persistent Dymola workers with per-test timeout watchdogs, a custom memmap MAT4 reader that beats `scipy.io.loadmat` by ~400× on large result files, piecewise event-boundary NRMSE, a tolerance-tube mode with time-varying control points, a live auto-refreshing dashboard, and an interactive Plotly report with drag-and-drop tube editing and live tolerance overrides exportable back to `test_spec.json`. The most direct overlap is **LBNL's BuildingsPy `regressiontest.Tester`**, not FMPy or MoPyRegtest — the target niche is "native Modelica libraries tested via Dymola," not FMU workflows. The tool's biggest liabilities are that it is **single-backend (Dymola only)**, has **no FMU or cross-simulator story**, and is **not packaged or distributed** beyond source. Verdict: **B — continue**, but scope decisions and a published backend story (OpenModelica or explicit "Dymola-only forever") are required before v0.1.

---

## Landscape Baseline

What already exists that's relevant:

**Dymola/Modelica library regression (closest category)**
- **BuildingsPy** (`lbl-srg/BuildingsPy`) — `regressiontest.Tester`, funnel comparator (x+y tolerance via pyfunnel), Dymola+OMC+Optimica, battle-tested at LBNL Buildings/IBPSA libraries. Command-line, Python-driven.
- **MoPyRegtest** — lightweight, `.mos`-based, `unittest`-driven, CI-first, tool-agnostic in principle.
- **OpenModelicaLibraryTesting** + its GitHub Action — nightly OM-centric suite with SQLite backend and HTML reports on GitHub Pages.
- **csv-compare** — raw CSV tolerance comparison utility.

**FMU-centric (adjacent, not in scope for this tool)**
- FMPy + pytest + numpy.testing.assert_allclose (the dominant Python pattern), pytest-regressions (`num_regression` for golden mgmt), PyFMI, FMI.jl, libcosim, OMSimulator, FMI Cross-Check.

**SciML / Julia**
- DiffEqDevTools.jl, SciMLBenchmarks.jl, FMI.jl ↔ MTK.jl cross-validation.

**Documented ecosystem gaps** (from landscape report and corroborated by all 4 LLM responses):
1. No cross-ecosystem unified framework.
2. No auto-update golden-file workflow (no `pytest --update-goldens` equivalent for simulation).
3. No standardized event-timing assertions (`assert event at t ≈ 0.5 ± 1ms`).
4. No stochastic / SDE regression outside SciML.
5. No general cross-simulator validation tooling beyond FMI Cross-Check.
6. CI compute cost + platform-dependent numerical drift are unsolved.
7. Proprietary (Dymola, Simulink) regression UX remains best-in-class but closed.

---

## Tool Overview

**What it does (plain language):** You point it at a Modelica library directory and it discovers tests either from `UnitTests` components embedded in `.mo` files or from an external `test_spec.json` (or both, merged). It generates per-test `.mos` scripts, simulates in parallel via persistent `DymolaInterface` workers (or batched `.mos` fallback), reads `dsres.mat` via a custom memmap parser, and compares each tracked variable against a stored reference JSON using piecewise NRMSE, tolerance tubes, or final-value-only. Failures produce a live dashboard, per-test Plotly reports with interactive tolerance editing, JUnit XML for CI, and a `--accept` / `--rerun` / `--merge` workflow for incremental iteration.

**What it claims to do better:** Compared to `.mos`-driven approaches (MoPyRegtest, OMLibraryTesting), it offers a modern interactive UX (tube editing, live tolerance overrides, exportable configs), per-phase timing diagnostics, persistent-worker parallelism, and a custom MAT4 reader that handles 76K-variable result files in seconds instead of ~7 minutes. Compared to BuildingsPy, it has a cleaner "tolerance travels with the baseline" model, a richer comparison-mode strategy (NRMSE/tube/points per variable), and a web-native debugging UI.

---

## Gap Analysis

| Gap | Rating | Evidence |
|---|---|---|
| **A. Unified framework across ecosystems** | **Does Not Address** | Dymola-only backend (`simulators/dymola/`), no FMU/OM/Julia path. Registry supports adding others (`@register` decorator, `simulators/__init__.py`) but only Dymola is implemented. |
| **B. Golden-file auto-update workflow** | **Addresses (strong)** | `--accept` bulk update; `-i [category]` interactive review; `--rerun failed,sim-failed` targeted rerun; `--merge` for partial-rerun full report; tolerances saved inside ref JSON (`comparison` block) so config travels with baseline. Stronger than pytest-regressions here — interactive tolerance editor exports a config applied via `spec-update`. |
| **C. Tolerance-based time-series comparison** | **Addresses (strong)** | Per-variable overrides, NRMSE with range-normalization (magnitude-normalization for constants, `comparator.py:216-221`), piecewise event-boundary splitting (`_find_event_boundaries`, `comparator.py:64-83`), three tube width modes (`rel`/`band`/`absolute`), time-varying tubes with control-point interpolation, resolve-then-interpolate for mixed-mode points (D26). Handles float32→float64 promotion noise explicitly. |
| **D. Event-detection assertions** | **Does Not Address** | No declarative event-time/event-order assertions. The closest feature is a *structural warning* when `EventCounter` final differs between runs (`comparator.py:459-500`) — useful but not a passing/failing assertion on event timing. |
| **E. Cross-simulator validation** | **Does Not Address** | Only one backend. Ref partitioning by `<Simulator>/<os>` (D3) is designed *for* this, but there's no comparison routine across partitions, and no FMU ingest path. Idea #36 lists this as future. |
| **F. CI/CD integration** | **Partially Addresses** | JUnit XML reporter (`reporting/junit_report.py`); CI example in README is 8 lines. No GitHub Action wrapper, no Git LFS story for large ref files, no cache key example. Dashboards and HTML reports are designed for local file://, not CI artifacts. |
| **G. Stochastic simulation regression** | **Out of Scope by Design** | No KS/ensemble/distribution comparison; no statement it ever will. Dymola is deterministic-solver territory. |

---

## Redundancy Analysis

| This tool does X | Existing tool that also does X | This tool's advantage |
|---|---|---|
| Dymola regression testing for Modelica libraries | **BuildingsPy `regressiontest.Tester`** (direct competitor) | Interactive Plotly tolerance editor with drag-and-drop tube editing; memmap MAT4 reader (BuildingsPy uses DyMat/scipy → slow on large files); piecewise event-boundary NRMSE; tolerance config stored inside ref JSON so it travels with baseline; persistent-worker parallelism with `dymola_lock` patch. |
| `.mos`-based Modelica test harness | **MoPyRegtest** | MoPyRegtest is lighter and tool-agnostic in principle but has no tube mode, no interactive UI, no auto-update, no dashboard, no event-boundary handling, no structural-change warnings. |
| Golden-file management + auto-update | **pytest-regressions `num_regression`** | pytest-regressions has no simulator abstraction, no Dymola driver, no event handling. Complements rather than competes. |
| Time-series comparison with tolerance | **csv-compare, numpy.testing.assert_allclose** | Feature-richer: piecewise events, tube envelopes, per-variable overrides, constant-signal normalization. These are low-level primitives; this is a framework. |
| FMU regression | **FMPy + pytest** | No overlap — this tool is native-Modelica, not FMU. FMPy is the right answer if you can export to FMU. |
| OpenModelica library regression | **OpenModelicaLibraryTesting + GH Action** | No overlap — this tool doesn't run OMC. OMLT is the right answer if you're OM-centric. |
| MAT4 file reading at scale | **scipy.io.loadmat, DyMat, BuildingsPy's IO** | ~400× faster on large result files (`docs/decisions.md` D18: 397s → <1s on 76K variables, 36MB file). This is a genuinely upstream-worthy contribution. |
| Persistent-worker Dymola parallelism | **None (all alternatives use batched .mos or per-test processes)** | The `dymola_lock` monkey-patch + `_find_available_port` narrow-lock + `_dymola_process.pid` attribution is unique. Non-trivial Dymola-internals work. |
| Interactive web-based tolerance tube editing | **None in open source** | Shift+click/drag/right-click on Plotly charts with live JSON export → CLI `spec-update`. Genuinely novel UX in this ecosystem. |

**Ruthless read:** If the goal is "regression-test a Dymola-driven Modelica library with a good developer UX," the nearest substitute is BuildingsPy — and this tool is better on interaction, performance, and correctness around events, but it is not yet packaged, not yet distributed, not yet battle-tested outside what appears to be a single library (TRANSFORM, referenced in the docs). The novelty claim is defensible; the production-readiness claim is not.

---

## Code Quality Notes

**Correctness** — comparison logic is careful and edge-case-aware:
- Event-boundary splitting handles Dymola's 2- *and* 3-duplicate-time-point cases (`comparator.py:64-83`); interior segments use pre-event dedup for bulk with post-event first-point override (lines 177-182). This is the kind of subtlety generic `np.interp`-on-CSV approaches silently get wrong.
- Constant-signal NRMSE normalizes by `max(|ref|)` instead of range (lines 216-221), avoiding false failures from float32 quantization on large-magnitude constants (D5 example: 512-unit error on 37e9 → 1.4e-8, not 512).
- Tube mode mixed-mode interpolation resolves to absolute y-values *first*, then interpolates (D26) — avoids discontinuities at mode boundaries.
- `dsres.mat` existence alone is explicitly insufficient; completion requires translation not aborted AND `dsfinal.txt` present AND reached-stop-time (D43, `constraints.md`). Partial `dsres.mat` from killed-mid-sim is a real failure mode this handles.
- Stale-artifact protection via `rmtree`+recreate before each run (D24) prevents silent false passes from previous runs.

**Robustness** — failure messages are specific (`Translation failed` / `Stopped early at T=4.7 of 10.0`), worker restart cap at 3, timeout watchdog with disk-check rescue ("lenient timeout" — sims that completed 1.5s past a 60s deadline get credit). The Windows atomicity of `status.json` writes uses unique tmp filenames plus a dedicated `_write_lock` because `os.replace` fails when another thread holds the file on Windows — someone has hit that bug in production.

**Extensibility** — `ComparisonMode` ABC with typed frozen configs (`modes.py`), `@register`-decorated simulator backends (`simulators/__init__.py`), Jinja2 templates for reports. Adding Frechet distance, ISO 18571, or pyfunnel x-tolerance (all in `ideas.md` #23-26) is a new `ComparisonMode` subclass. Adding OpenModelica is a new `@register("OpenModelica")` runner. The architecture supports extension; it hasn't been *exercised* for extension.

**Testability** — 2,400 LoC of pytest covering comparator (955 lines — the bulk), storage round-trips, simulator mocks, config resolution, discovery, CLI, with a `@pytest.mark.dymola` marker for Dymola-dependent tests. Real Dymola artifacts (`dsres.mat`, `dslog.txt`) are checked into `tests/fixtures/` — lets the MAT4 reader and log parser be tested without Dymola. This is appropriate and non-trivial.

**Packaging** — `pyproject.toml` with hatchling, console script `dstf = dstf.cli:main_entry`, `uv tool install dstf` instructions. **But: not on PyPI, not tagged, version `0.1.0` with no releases.** The README assumes `uv run`; a PyPI release would dramatically lower adoption friction.

**Performance** — the MAT4 memmap reader is the standout (D18). Parallelism via persistent workers + queue-dispatched batches (D35, D41). HTML reports get scattergl/LTTB items on the ideas list (#15-16) for >10k-point traces — currently all-SVG, will struggle at scale. Plotly via CDN means reports need internet to render.

**Documentation** — `CLAUDE.md`, `README.md`, and `docs/{architecture,decisions,constraints,patterns,ideas,usage}.md` total ~45KB, and the decisions log is genuinely useful — 40+ entries explaining *why*, not just *what*. This is above average for a prototype. What's missing is a user-facing "first ten minutes" onboarding for someone *not* already invested.

---

## Verdict: **B — Continue. Genuine gap, but significant work remains.**

## Rationale

**Not Verdict A (use existing tools):** The tool is not substantially redundant. BuildingsPy is the nearest alternative; this beats BuildingsPy on four concrete axes — MAT4 read performance at scale (D18), event-boundary piecewise NRMSE, tolerance-tube interactive editing UX, and persistent-worker parallelism. FMPy+pytest doesn't apply to native Modelica/Dymola workflows that skip FMU export. MoPyRegtest is lighter but meaningfully less capable. The specific combination — "Dymola-native + modern interactive UX + performant large-result handling" — does not exist elsewhere in open source.

**Not Verdict C (strong invest):** Single-backend is a strategic cap on how broadly this can matter. Without an OpenModelica backend (or a deliberate, documented "Dymola-forever" scope statement), the addressable user base is narrower than the implementation effort implies. Not packaged on PyPI, no GitHub Action, no CI example beyond 8 README lines, no evidence of adoption outside one reference library. Several of the most user-visible strengths (interactive Plotly editing, live dashboard) are hard to demo in a CI pipeline, which is where open-source simulation testing lives.

**B fits:** Real gap, real differentiation, implementation is honest and careful, but the product surface is narrow and not yet packaged/distributed for outside users to pick up.

---

## Action Items

```
P0 (Blocking — fix before any external use):

  [ ] Ship a PyPI release (v0.1.0). `uv tool install dstf` is the advertised install path;
      today it only works from a local checkout. Without PyPI, "continue development" has no users.
  [ ] Write an explicit scope statement: "Dymola-only in v0.x; OpenModelica under consideration for
      v0.y" OR commit to an OM backend for v0.2. The tool's simulator registry implies multi-backend
      but only ships Dymola. Leaving this ambiguous costs credibility with evaluators.
  [ ] Minimum-viable GitHub Actions example (not just the 8 README lines). Include:
        - self-hosted runner caveat (Dymola isn't installable in GH-hosted runners)
        - Git LFS for reference JSONs (they can be MB-sized)
        - JUnit report artifact upload + test-reporter annotation
      Without this, "CI-ready" is aspirational.
  [ ] First-ten-minutes docs for someone who has never heard of UnitTests/test_spec. Today's README
      jumps straight to advanced usage. The landscape reports identify onboarding as a documented
      pain point.

P1 (High-value — should complete before v0.1 public release):

  [ ] Extract the MAT4 memmap reader into its own package (`dymola-mat` or similar). This is a
      general-purpose utility that BuildingsPy, DyMat, and any Dymola-result-consuming tool would
      benefit from. Upstreaming to DyMat is probably the highest-leverage move.
  [ ] OpenModelica backend (even a minimal one) — the simulator abstraction exists; exercise it. OMC
      produces .mat in the same basic format; reuses most of mat_reader.py. Unlocks cross-simulator
      validation (Gap E) as a future story.
  [ ] Implement x-direction time tolerance (ideas.md #25, pyfunnel integration). The landscape
      report and every LLM response call out solver-dependent event-timing shifts as an unsolved
      source of false failures. pyfunnel is pip-installable; this is a 1-2 day integration.
  [ ] Declarative event-timing assertions (Gap D). Even a simple form: `{"events": [{"variable":
      "phase", "time": 0.5, "time_tolerance": 0.001}]}` in test_spec.json. Novel in the ecosystem.
  [ ] HTML-report scalability: scattergl swap above 5k points + LTTB decimation above 10k
      (ideas.md #15-16, ~70 lines JS). Without this, reports degrade fast on realistic suites.
  [ ] Offline Plotly bundling flag (reports currently require CDN access). Users in air-gapped
      environments (common in power/defense simulation) can't use reports at all today.

P2 (Nice to have — post-MVP):

  [ ] Cross-platform / cross-simulator comparison mode (ideas.md #36).
  [ ] Frechet / ISO 18571 comparison modes (ideas.md #23, #26) — add alongside NRMSE as diagnostics.
  [ ] FMU ingest path (FMPy-backed runner). Opens the entire FMU ecosystem as a target, not just
      Dymola-compiled Modelica.
  [ ] Spectral coherence (ideas.md #24) for oscillatory models.
  [ ] Parallelize report generation (ideas.md #32) — matplotlib PNGs dominate on large suites.
  [ ] Optional FastAPI server mode for accept-from-browser (ideas.md #29).

Ecosystem contributions (upstream improvements):

  [ ] Upstream the MAT4 memmap reader to DyMat or release as standalone (see P1). This alone would
      be cited by downstream users even if they never adopt ModelicaTesting.
  [ ] Contribute tube-comparison patterns to pytest-regressions or BuildingsPy. The tube-mode
      strategy + interactive editor is a reusable design.
  [ ] Propose a standard `comparison` block format for reference files (tolerance + variable
      overrides travelling with the baseline). The landscape report identifies "tolerances travel
      with baseline" as an unsolved ecosystem problem; this tool has a credible answer.
```

**Minimum viable v0.1:** P0 complete + at least one of {P1 OpenModelica backend, P1 pyfunnel x-tolerance}. Without either of those two P1 items, the tool is "a nicer BuildingsPy for one library" rather than "a genuinely new point in the design space."
