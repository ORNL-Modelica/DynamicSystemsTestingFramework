# Session handoff — DSTF rename + reporter-as-IDE feature-complete

**Date**: 2026-04-25
**Covers**: D80 through D85 (seven-arc session)
**State at HEAD** (commit `a05b829`):
- **837 tests passing + 0 skipped, 0 regressions**
- **5 simulator backends**: Dymola, FMPy, OpenModelica, Julia/MTK, Python
- **3 test libraries**: `ModelicaTestingLib` (11 tests), `JuliaMtkTestingLib` (8 tests), `PythonTestingLib` (3 tests) — points-mode parity test added everywhere
- **All 6 comparison modes window-aware end-to-end in JS** (NRMSE, tube, points, range, event-timing CLI-authoritative, dominant-frequency)
- **All declared-list modes have editor surfaces** (event-timing, dominant-frequency, points — table + add/delete + detect/snapshot)
- **Points editor is feature-complete in the browser**: multi-slot mount, shift+click/drag/right-click parity with tube + dom-freq via `createPointPlotEditor`, native Plotly shape-drag for box resize with relayout-listener mirror, mode-switch tolerance conversion with near-zero protection, table updates live on every state change.

**Naming**: As of D81, the tool is **Dynamic Systems Testing Framework (DSTF)**; CLI is `dstf`; Python import root is `dstf`. Historical plans/specs under `docs/superpowers/` and D1–D79 entries in `docs/decisions.md` retain the old `modelica-testing` / `final-only` names — that's by design (snapshots of past state).

---

## Session arc

Six conceptually distinct arcs over one long session. Each is a multi-commit unit on `main`:

| Arc | Range | Theme |
|---|---|---|
| **D80** | `9e1954e` → `53eaa34` (11 commits) | Python-driven tests as a 5th backend — `PythonRunner` + `PythonTestingLib` (SimpleRamp scipy + ConstantCsv pure-data-loader). The CSV loader is the architectural proof that the backend abstraction isn't secretly simulator-shaped. |
| **D81** | `37e7056` → `78bd4da` (4 commits) | Full tool rename: `modelica_testing` → `dstf`, `--final-only`-style flag aliases swept too, brand throughout docs + memory + settings. Clean break, no back-compat alias. |
| **Range fixes + cross-metric consistency** | `821b3c2` → `959a92d` (6 commits) | systematic-debugging surfaced 4 cross-cutting issues: range JS scorer ignored window (Bug 1), bound lines drew full-width (Bug 2), tube scorer had latent same-class bug, nrmse/final-only used CLI-cached values (silent no-op on window edits). Extracted `_sliceLeafTrajectory` helper, applied to every JS scorer + plot decorator. Bug 3 (Plotly autorange-stuck-after-shape-edit) characterized as non-reproducing via state-mutation path; left a regression test as guard. Cross-metric matrix locked in as parameterized regression. |
| **D82** | `4647ee4` → `38ff4a3` (5 commits) | Event-timing declared-events editor. CLI gained `events: Optional[list[dict]]` field; declared list claims nearest auto-detected actual within per-event tolerance. JS editor: table + detect-from-{ref,actual} + live match column. Stays CLI-authoritative for pass/fail. |
| **D83** | `bd626a9` (1 commit, run in background) | Baseline-free NO_REF short-circuit. `ComparisonMode.is_baseline_free()` overridable per mode; comparator skips NO_REF when every leaf scorer can run without ref. Surfaced + fixed two adjacent bugs: `resolve_mode` wasn't forwarding `events` to EventTimingConfig (D82 oversight) and `_compare_dominant_frequency` unconditionally required ref FFT. |
| **D84** | `e975aae` → `0b8d0b2` (9 commits) | Final-only → **points** mode rename + capability expansion. Per-point ref-relative or absolute targets, abs/rel y-tolerance modes, **x-axis tolerance (`time_tolerance`)** for solver-timing-drift cases. New JS editor with table, "📸 Snapshot from ref" button, zero-point fast-path placeholder. Plot: diamond marker + translucent tolerance box per point. |
| **D85** | `5a02d9f` → `a05b829` (8 commits, 2026-04-25) | Range autorange fix (transparent-marker scatter trace per declared bound, so double-click reset includes the bound) + cross-library points parity tests + points editor polish. Editor polish: multi-slot mount via `querySelectorAll`, shift+click/drag/right-click via `createPointPlotEditor`, Plotly-native box resize wired through a `plotly_relayout` listener that re-derives `pt.time_tolerance` and `pt.tolerance` from new bounds and snaps back centered. Mode-switch tolerance conversion (abs↔rel) preserves visible box size; near-zero target protection rejects abs→rel switches that would produce > 1000% rel fractions. Closes ideas.md #61 (points draggable plot markers, full scope). |

Total: 44 commits.

Each arc has a corresponding plan + spec under `docs/superpowers/`:
- `plans/2026-04-24-python-driven-tests.md` — D80
- `plans/2026-04-24-dstf-rename.md` — D81
- `plans/2026-04-24-range-metric-fixes.md` — Range fixes
- `plans/2026-04-24-event-timing-editor.md` — D82
- `specs/2026-04-24-points-mode-design.md` + `plans/2026-04-24-points-mode.md` — D84

D83 + D85 had no plan files (D83 a single small fix in-conversation; D85 a sequence of regression-by-regression fixes from real-browser feedback, each commit message capturing what was learned).

---

## Dev env (unchanged)

```bash
# Core (post-rename)
uv pip install -e ".[dev,fmpy,om]"
uv pip install pytest-playwright
uv run playwright install chromium

# Julia (D77+)
curl -fsSL https://install.julialang.org | sh -s -- -y --default-channel 1.11
cd examples/julia/JuliaMtkTestingLib && julia --project=. -e 'using Pkg; Pkg.instantiate()'

# Python (D80+)
uv pip install scipy   # for SimpleRamp; ConstantCsv has stdlib-only deps
```

**Venv drift caveat** (persisted): `uv run` may resolve miniforge3's pytest, NOT the project venv. If a hard dep errors as missing, install into whichever Python `uv run which pytest` points at (typically `/home/fig/miniforge3/bin/pip install X`).

---

## Backends (5, all production)

| Backend | Runner | Transport | Persistent | Typical use |
|---|---|---|---|---|
| **Dymola** | `DymolaRunner` | Python interface (default) / `.mos` batch | ✓ default | Proprietary Modelica |
| **FMPy** | `FmpyRunner` | `fmpy.simulate_fmu` in-process | per-test thread | Pre-built FMUs |
| **OpenModelica** | `OpenModelicaRunner` | OMPython ZMQ (default) / `omc` batch | ✓ default | Open-source Modelica |
| **Julia / MTK** | `JuliaRunner` | subprocess (batch) / stdin-JSON pipe (D78 persistent) | ✓ default | ModelingToolkit / Dyad (Dyad untested) |
| **Python** | `PythonRunner` | subprocess per test (batch) | ✗ MVP | Arbitrary Python: scipy, CSV, pandas, HTTP, ... |

References partition by `<reference_root>/<Backend>/<os>/ref_NNNN.json`. CLI's `_get_runner(persistent=True)` swaps to persistent variants where available; falls back to batch on `RuntimeError`.

---

## Reporter-as-IDE — feature complete

| Mode | CLI window-aware | JS live scorer window-aware | Plot decoration window-aware | Editor |
|---|---|---|---|---|
| nrmse | ✓ | ✓ (live-ported D-range) | N/A | auto-derived inputs |
| tube | ✓ | ✓ (D-range fix) | ~ (delegates to editor when active) | shape-drag + control-point editor |
| points (D84/D85) | ✓ | ✓ | ✓ (translucent box + diamond, native shape-drag for resize) | declared-points table + Snapshot + shift+click/drag/right-click |
| range | ✓ | ✓ (D-range fix) | ✓ (dual-style gray/red, D-range fix) | shape-drag |
| event-timing | ✓ | N/A (CLI-authoritative) | ✗ (overlay only) | declared-events table + Detect (D82) |
| dominant-frequency | ✓ | ✓ | ✓ (spectrum subplot) | declared-peaks table + Detect + draggable markers |

Cross-cutting helpers introduced D-range:
- `_sliceLeafTrajectory(leaf, traj)` — reads `leafState[leaf.path].window`, returns `{refTime, refValues, actTime, actValues}` clipped. Used by every window-aware scorer.
- `_minDeltaInBox(times, values, tLo, tHi, target)` — D84 — Python `_min_delta_in_box` mirror with zero-crossing detection on adjacent linear segments. Used by points scorer + plot decorator.

---

## Plan-quality lessons learned this session

Worth carrying forward to future plans:

1. **Exhaustive grep before listing files**: D84 Task 1 listed 4 source files to rename; subagent found 3 more with module-level imports of the renamed class (`schema_export.py`, `tree_spec.py`, additional `comparator.py` call-sites). Future rename plans should `grep -rln <old-name> src/` before drafting the file list.
2. **Algorithm sanity check on new helpers**: D84 Task 4's `_min_delta_in_box` initially missed zero-crossing detection — a piecewise-linear curve can pass through target between samples without any sample seeing delta=0. Subagent caught it; I propagated the fix to the JS port in Task 5 by calling it out explicitly. Lesson: if a helper claims "find min over [a, b] of a piecewise-linear function," it must either evaluate at vertices OR detect zero-crossings.
3. **Sed identifier vs string**: D84 Task 9's broad `final_only → points` sed swept too aggressively, rewriting 5 places where `final_only` was a Python identifier (parameter, CLI flag) when they should have been `default_points`. Manual skim caught all of them; future plans should distinguish identifier renames from string-content renames.
4. **Pre-known plan corrections accumulate**: each plan inherits the prior session's discovered corrections. The set as of D84:
   - Playwright test imports: `from test_interactive_playwright import (...)` not `from tests.test_interactive_playwright`.
   - `leafState` is a script-scope const, not `window.leafState`.
   - Global tree variable is `TREE_VIEW`, not `SPEC_TREE`.
   - `initLeafState()` merges `params` with `mode_values` (mode_values wins) — fixture overrides need both.
   - Don't rely on linspace-grid float-exactness; use piecewise-constant or hand-tuned trajectories for deterministic tests.

---

## Known limitations (deferred by design)

| Item | Why | Workaround |
|---|---|---|
| Event-timing live JS scorer | Event-pairing algorithm non-trivial; CLI authoritative | Pill shows CLI result until next CLI rerun |
| Tube per-point-per-side width modes | JS UI stores; polygon uses global mode | Use synced mode |
| Window brush one-shot per activation | UX choice | Click brush again to redo |
| Multi-select wrap in tree editor | Deferred | Wrap single, then move siblings |
| JuliaRunner FMU export | `MTK.generate_fmu` not wired | Run directly via Julia runner |
| Persistent-worker Python | D77→D78 progression not yet applied | Subprocess-per-test sufficient for typical suites |
| Dyad tests | Untested (should work — compiles to MTK) | Port a sample when concrete need arises |
| Bug 3 reproduction (Plotly autorange-stuck) | Doesn't reproduce via state-mutation path | Characterization test guards current behavior; investigate again with concrete in-browser repro |
| ~~Points mode draggable markers~~ | DONE in D85 | — |
| Points mode live edge mirror during drag | Plotly doesn't fire `plotly_relayouting` for shape edits — only the final `plotly_relayout` on release. True live mirror would require disabling shape-edit globally and reimplementing range + points box drag through custom mousedown/move/up | Snap-on-release accepted as the UX for now. Unblocks if revisited. |
| ModelicaTestingLib EventTest / IntervalTest / NoUnitTest / SimulateOnlyTest on Julia | Deferred | — |
| PointsCheckTest Dymola/windows reference | Generated locally for OM/Julia/Python/FMPy; Dymola needs Windows host | `dstf run --filter "*PointsCheckTest" --accept` from a Windows shell |

---

## Pre-session sanity

```bash
git log --oneline -15                                         # confirms D80..D85 + bug-fix arcs
uv run pytest -q                                              # expect 837 passed + 0 skipped, 0 failures
export PATH="$HOME/.juliaup/bin:$PATH" && uv run pytest -q    # same on Julia-installed envs

# Smoke tests (each should produce PASS):
uv run dstf --config examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json run
uv run dstf --config examples/julia/JuliaMtkTestingLib/Resources/ReferenceResults/testing.json run
uv run dstf --config examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json run
uv run dstf --config examples/fmu/testing.json run   # requires reference-fmus-binaries/
```

---

## Candidate next moves

User's explicit roadmap from start of session, with status at HEAD:

| # | Item | Status |
|---|---|---|
| 1 | DSTF rename | ✓ D81 |
| 2 | Range metric bugs + cross-metric standardization | ✓ Range-fix arc |
| 3 | Event-timing HTML editor | ✓ D82 |
| 4 | Final-only → point-based | ✓ D84 |
| 5 | Baseline-free NO_REF short-circuit | ✓ D83 |
| 6 | Code review / tech-debt review | **pending** |
| 7 | New capabilities (experiment alignment, Dyad, Julia recognizer, FMU export matrix) | **pending** |
| 8 | Docs cleanup throughout (user guides, technical manual) | **pending** |

### Next session — top-of-stack candidates

**B-tier (recommended starts)**

* **#6 Code review + tech-debt sweep** (the natural consolidation point after D80–D85 added substantial code surface; particularly attractive now that points editor is settled and the abstraction line between range / points / tube box editing is visible). Likely outputs: extract shared "declared-items table editor" between dom-frequency, event-timing, and points (overlap audit in D82 said 74% against the smaller editor; points adds another instance). Audit `interactive.js` line count (~2400 LOC, growing) and consider splitting into modules. Inspect for dead code (3 dead branches in `plot_comparison.py` were swept in D84 Task 9; there may be more). Optional sub-arc: disable global `edits.shapePosition`, reimplement range + points box drag through custom mousedown/move/up — would unlock true live mirror during the drag (currently snap-on-release per D85 limitation). ~1-3 days depending on appetite.

* **#7 capabilities**: pick one to start with —
  - **Experiment-data alignment preprocessing** (ideas.md #57). Preprocessing `ComparisonMode` wrapper that does time-offset/amplitude-scale alignment before scoring. The `points` mode with `time_tolerance` already covers the discrete-checkpoint case; this would be the continuous-trajectory variant (pyfunnel-style). Needs a concrete user use case before building (D66 economy-of-tools guard). ~3-5 days.
  - **MTK FMU export** via `ModelingToolkit.generate_fmu` (~1 day). Wires Julia into the `Capability.FMU_EXPORT` cross-backend chain.
  - **Dyad validation** (~½ day). Port one Dyad sample, prove the "Dyad → MTK → runner" claim.
  - **Julia source recognizer** (~1 day). Auto-discover Julia tests from `.jl` files (parallel to Modelica `.mo` UnitTests-annotation discovery). Removes the need to hand-author `test_spec.json` for Julia libraries.

* **#8 Docs cleanup throughout**. Each prior arc folded its own doc updates into commits, so the current state of docs is self-consistent — but there's no top-level user guide tour. README.md was flagged as substantially out of date (still describes only Dymola). ~1-2 days for a comprehensive sweep + new-user "first 5 minutes" walkthrough.

**Smaller follow-ups (C / D-tier)**

* **Persistent-worker Python** (ideas.md #58). Mirrors Julia D77→D78. Pays off for suites with hundreds of Python tests. Defer until perf ceiling hits.
* ~~**Points mode draggable markers** (ideas.md #61).~~ Done in D85.
* **Shared declared-items editor extraction** (D82 follow-up; ideas.md #60). 74% overlap measured; D85 added points as the third instance, strengthening the case. Would shrink event-timing editor dramatically.
* **Live edge mirror for box-resize drag** (D85 follow-up). Requires disabling global `edits.shapePosition` and reimplementing range + points box drag as custom mousedown/move/up handlers. Snap-on-release UX is acceptable today; revisit if any user actually misses live mirror.
* **#53 `check-openmodelica` / `check-julia` CLI subcommands**. Symmetric with `check-dymola`.
* **#54 OM FMU export** via `buildModelFMU`.
* **#47 time-array dedup** / bump embedded-sample cap.
* **Visual-regression Playwright screenshots**.
* **Phase 9 dataset types** (E-tier foundational): Events / Spectrum / Distribution / Scalars / Field. Unlocks Fréchet (#23), spectral coherence (#24), pyfunnel x-tolerance (#25), ISO 18571 (#26).

---

## Starter prompt for the next session

> Resuming DSTF (Dynamic Systems Testing Framework) at commit `a05b829` on `main`. The previous session was a 7-arc marathon (D80 → D85) that:
> - Added Python as a 5th backend with the architectural-proof CSV-loader test (`ConstantCsv`).
> - Renamed the tool from ModelicaTesting to DSTF (D81).
> - Fixed three range-metric bugs and locked in cross-metric window-edit consistency for every mode (extracted `_sliceLeafTrajectory` helper).
> - Added the event-timing declared-events editor (D82) and the points-mode rename + capability expansion (D84) including x-axis tolerance.
> - Added baseline-free NO_REF short-circuit (D83).
> - Polished the points editor to feature-complete in the browser (D85): multi-slot mount, shift-modifier parity, native shape-drag for box resize via `plotly_relayout` listener, mode-switch tolerance conversion, near-zero protection. Plus range double-click autorange fix and cross-library points parity tests.
>
> **State at HEAD**: 837 passing, 0 skipped, 0 regressions. 5 backends, 3 fixture libraries (each now has a points-mode example). Reporter-as-IDE is feature-complete and has been validated by real-browser testing across the points editor surface.
>
> **Read first**: `docs/SESSION_HANDOFF.md` (this file), D80–D85 in `docs/decisions.md`, and the candidate next moves at the bottom of this file.
>
> **Default next move — pick one**:
>
> 1. **#6 Code review + tech-debt sweep** (~1-3 days). Natural consolidation after D80–D85 added substantial code surface. Likely outputs: extract shared declared-items editor (74% overlap measured, with points now the third instance), audit `interactive.js` line count (~2400 LOC), sweep for more dead branches. Optional sub-arc: disable Plotly's global `edits.shapePosition` and reimplement range + points box drag as custom handlers (would unlock live mirror during drag — D85's standing limitation).
>
> 2. **#7 New capability — pick one**: experiment-data alignment preprocessing (~3-5 days, needs concrete use case), MTK FMU export (~1 day), Dyad validation (~½ day), or Julia source recognizer (~1 day). Funnel mode (#25) — pyfunnel-style continuous x+y tolerance — is also tracked here; the points-mode `time_tolerance` covers the discrete case but the continuous version is a separate ComparisonMode.
>
> 3. **#8 Docs cleanup** (~1-2 days). README.md is substantially out of date — still describes only Dymola. Add a "first 5 minutes" walkthrough.
>
> **Smaller alternatives**: persistent-worker Python, shared declared-items editor extraction (D82 follow-up, now stronger after D85 added points as a 3rd instance), live edge mirror via custom drag handlers (D85 limitation).
>
> **Dev env prereqs** (if venv was recreated): `uv pip install -e ".[dev,fmpy,om]"` + pytest-playwright + chromium. Julia: `cd examples/julia/JuliaMtkTestingLib && julia --project=. -e 'using Pkg; Pkg.instantiate()'`. Python scipy for SimpleRamp + PointsCheck examples. Watch venv drift (miniforge vs project .venv).
>
> **Out of scope unless explicitly adopted**: Phase 9 dataset types, ML-driven anything, mid-stream refactor of stable abstractions (ComparisonMode / SimulatorRunner / MetricTree).
