# Session handoff — Julia/MTK backend + reporter expansion + tool growth

**Date**: 2026-04-23
**Covers**: D71 through D79 (nine-phase session)
**State at HEAD** (commit `9cd5468`):
- **761 tests passing, 1 skipped (reference_fmus), 0 regressions**
- **5 simulator backends**: Dymola, FMPy, OpenModelica, Julia/MTK, Python
- **3 test libraries**: `ModelicaTestingLib` (10 tests), `JuliaMtkTestingLib` (7 tests), `PythonTestingLib` (2 tests)
- **14 cross-library companion overlays** (both directions, portable paths)
- Reporter-as-IDE feature-complete through declared-peaks frequency + live JS FFT

---

## Session arc

Nine interconnected phases over one long session. Each is a
single-commit unit on `main`:

| # | Commit | Theme |
|---|---|---|
| D71 | part of `ae7dabc` | Feature-showcase tests + NB overlay parity + simulate-only fix |
| D72 | part of `ae7dabc` | Wrap/unwrap + combinator-kind editing |
| D73 | part of `ae7dabc` | Leaf-state persistence + reset button |
| D74 | part of `ae7dabc` | Labels/help/tooltips + visibility sync + event-timing plot + multi-peak freq + spectrum subplot |
| D75 | part of `ae7dabc` | Declared-peaks dominant-frequency + PointPlotEditor abstraction |
| Q2 fixes | part of `ae7dabc` | Tube polygon curve-following + quiet patch export |
| D76 | `6595228` | Live JS FFT + window-scoped detection + per-peak provenance |
| post-D76 | `06cc096` | Five dominant-frequency Windows-found bugs |
| D77 | `d6c43f8` | Julia/MTK backend (batch subprocess) |
| D78 | `813463e` | Persistent-worker Julia + cross-library companions (first 4) |
| D79 | `9cd5468` | JuliaMtkTestingLib structure + 5 new ports + observables fix |

Plus docs: `6962770` refreshed SESSION_HANDOFF + architecture + ideas
mid-session. This file is the post-D79 replacement.

---

## Dev env (required)

```bash
# Core
uv pip install -e ".[dev,fmpy,om]"
uv pip install pytest-playwright
uv run playwright install chromium

# Julia (NEW this session)
curl -fsSL https://install.julialang.org | sh -s -- -y --default-channel 1.11
# Then (first-time only — multi-minute precompile of MTK + OrdinaryDiffEq):
cd examples/julia/JuliaMtkTestingLib && julia --project=. -e 'using Pkg; Pkg.instantiate()'
```

**Venv drift caveat** (persisted from prior sessions): `uv run pytest`
on this machine resolves miniforge3's pytest, NOT the project venv.
If a hard dep errors as missing, install into whichever Python
`uv run which pytest` points at — typically `/home/fig/miniforge3/bin/pip install X`.
Playwright, psutil, pytest-playwright have all hit this.

---

## Backends (four of them, all production)

| Backend | Runner | Transport | Persistent? | Library | Typical use |
|---|---|---|---|---|---|
| **Dymola** | `DymolaRunner` | Python interface (default) / `.mos` batch | ✓ (default) | — | Proprietary Modelica |
| **FMPy** | `FmpyRunner` | `fmpy.simulate_fmu` in-process | per-test thread | — | Pre-built FMUs (autonomous only; D65 scope) |
| **OpenModelica** | `OpenModelicaRunner` | OMPython ZMQ (default) / `omc` batch | ✓ (OMPython) | — | Open-source Modelica |
| **Julia / MTK** | `JuliaRunner` | subprocess (batch) / stdin-JSON pipe (D78 persistent) | ✓ (D78) | — | ModelingToolkit / Dyad (Dyad untested but should work) |
| **Python** | `PythonRunner` | subprocess per test (batch) | ✗ (MVP) | — | Arbitrary Python: scipy, CSV, pandas, HTTP, ... |

All four declare `capabilities: frozenset[Capability]` (`BATCH_FALLBACK`,
`PERSISTENT_WORKERS`, `FMU_EXPORT`). CLI's `_get_runner(persistent=True)`
swaps to the persistent variant when available; falls back to batch on
`RuntimeError`.

Reference baselines partition by `<reference_root>/<Backend>/<os>/ref_NNNN.json`.

---

## Current architecture (post-D79)

### Test libraries

```
examples/
├── modelica/
│   └── ModelicaTestingLib/           (10 tests, Modelica source)
│       ├── Components/UnitTests.mo
│       ├── Examples/*.mo
│       ├── Resources/ReferenceResults/
│       │   ├── testing.json, test_spec.json
│       │   ├── Dymola/windows/ref_0001..0010.json
│       │   └── OpenModelica/linux/ref_0001..0010.json
│       │       └── companions/ref_*/julia-mtk-*.json  ← cross-lib
│       ├── package.mo, package.order
│
├── julia/
│   └── JuliaMtkTestingLib/           (7 tests, MTK source, NEW D79)
│       ├── Project.toml, Manifest.toml
│       ├── Examples/
│       │   ├── SimpleRamp.jl   (↔ SimpleTest)
│       │   ├── Constant.jl     (↔ ConstantTest)
│       │   ├── Frequency.jl    (↔ FrequencyTest)
│       │   ├── MultiFrequency.jl (↔ MultiFrequencyTest)
│       │   ├── RangeCheck.jl   (↔ RangeCheckTest)
│       │   ├── TubeTolerance.jl (↔ TubeToleranceTest)
│       │   └── MetricTree.jl   (↔ MetricTreeTest)
│       └── Resources/ReferenceResults/
│           ├── testing.json, test_spec.json
│           └── Julia/linux/ref_0001..0007.json
│               └── companions/ref_*/modelica-om-*.json ← cross-lib
│
└── fmu/
    └── reference-fmus-binaries/      (3 tests, prebuilt FMUs)
```

### Sibling vs companion (locked this session, D78/D79)

* **Sibling** = same code, different simulator. Auto-discovered via
  `<reference_root>/<OtherBackend>/<os>/ref_*.json` scan. E.g.,
  ModelicaTestingLib tests on Dymola vs OM vs FMPy.
* **Companion** = different implementation, visual-only overlay.
  Manually wired via `companion add` with path string (absolute or
  repo-relative). JuliaMtkTestingLib ↔ ModelicaTestingLib uses this
  venue. Never scored.

### Reporter-as-IDE (feature-complete for six modes)

Recursive `SpecNodeView` JS component. Every leaf mode has:

* Auto-derived control panel (`render_schema_html` from each
  `ModeConfig` dataclass), optional custom panel.
* Live JS scorer (nrmse, tube, range, final-only, dominant-frequency —
  event-timing stays CLI-authoritative).
* Plot contribution (on trajectory plot: range lines, tube polygon
  with curve-following bounds, event instants + tolerance bands,
  window highlight).

Structural editing (D72/D73):
* `+ −` buttons (add/remove child leaves).
* Kind `<select>` in every combinator header (5 options: and/or/warn/
  k-of-n/weighted).
* `⊕` wrap / `⊖` unwrap buttons.
* `↻` reset leaf params to CLI-evaluated originals.
* All edits emit wholesale `/metrics` RFC-6902 replace.

Dominant-frequency editor (D75+D76):
* Spectrum subplot with live JS FFT (recomputes on window edit).
* Declared-peaks table (`Freq | Tolerance | Mode | Match (live) | Src window | ✕`).
* `Detect from: [Reference | Actual]` dropdown scoped to current window.
* Per-peak `derived_from_window` provenance metadata.
* Diamond markers draggable via Shift+click/drag/right-click via the
  shared `PointPlotEditor` factory (also powers tube editor).

---

### D80 — Python-driven tests (this session)

* New backend `PythonRunner` (`src/dstf/simulators/python/`)
  mirroring the Julia D77 pattern: framework-shipped `run_test.py`
  driver loads the user's `.py` file via `importlib.util`, calls
  `simulate(stop_time, tolerance) -> dict`, writes a JSON result.
* New fixture library `examples/python/PythonTestingLib/` with two
  tests: `SimpleRamp` (scipy-based ODE, counterpart to
  ModelicaTestingLib/JuliaMtkTestingLib SimpleTest/SimpleRamp) and
  `ConstantCsv` (CSV loader — *zero* ODE code, architectural proof
  that the backend abstraction is not secretly simulator-shaped).
* Minor refactor: `spec_parser.py`'s `julia_rel` → `source_rel`
  variable rename to reflect that `"source"` is the generic
  non-Modelica source-file field.
* Batch-only MVP; persistent-worker Python deferred (same D77→D78
  progression as Julia).

## Known limitations (deferred by design)

| Item | Why | Workaround |
|---|---|---|
| Event-timing live JS scorer | Event-pairing algorithm non-trivial; CLI stays authoritative | CLI pass/fail is correct; browser pill shows CLI result |
| Tube per-point-per-side width modes | JS UI stores them but polygon uses global mode (matches CLI) | Use synced mode |
| Window brush one-shot per activation | UX choice | Click brush again to redo |
| Multi-select wrap in tree editor | Deferred | Wrap single node, then add/move siblings |
| JuliaRunner FMU export | `MTK.generate_fmu` not wired | Run directly via Julia runner |
| Julia persistent workers + crash recovery | 3× restart on catastrophic failure | Same pattern as Dymola/OM |
| Dyad tests | Untested (should work — compiles to MTK) | Port a Dyad sample when concrete use case arises |
| EventTest / IntervalTest / NoUnitTest / SimulateOnlyTest on Julia | Deferred (see D79) | — |

---

## Pre-session sanity

```bash
git log --oneline -8                                          # confirms D71..D79 + docs commits

uv run pytest -q                                              # expect 749-752 passed + 1-4 skipped
export PATH="$HOME/.juliaup/bin:$PATH" && uv run pytest -q    # with Julia: 752 + 1 skipped

# Smoke tests (each should produce PASS):
uv run dstf --config examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json run
uv run dstf --config examples/julia/JuliaMtkTestingLib/Resources/ReferenceResults/testing.json run
uv run dstf --config examples/fmu/testing.json run   # requires reference-fmus-binaries/
```

---

## Candidate next moves

### B-tier (user-facing features)

* **Phase 7 rule-based recommender** — signal → MetricTree proposals
  bounded by D66's complexity budget. Lowers onboarding barrier for
  new users. ~2-3 days skeleton, ~1-2 weeks full.

* **TRANSFORM upstream portability PR** — 46 `each`-modifier fixes
  from the D69 OM sweep. External repo. Lifts OM pass rate 72% → ~85%.
  ~1 day. **Still the fastest real-world-impact move** if TRANSFORM
  adoption matters.

* **#45 Python-driven tests** — pairs naturally with the now-proven
  subprocess+`TestResult` contract (Julia subprocess path is basically
  exactly what #45 needs, just substituting `python` for `julia`).
  ~2-3 days for a credible MVP. Would unlock pyomo/scipy/custom solver
  use cases.

### Julia follow-ups (C-tier — ship opportunistically)

* **MTK FMU export** via `ModelingToolkit.generate_fmu`. Wires Julia
  into the `Capability.FMU_EXPORT` cross-backend chain. ~1 day.
* **Dyad validation** — port one Dyad sample test and prove the
  "Dyad compiles to MTK → our runner handles it" claim. ~½ day.
* **Port the 4 deferred Modelica tests** (EventTest, IntervalTest,
  NoUnitTest, SimulateOnlyTest) — each has its own small wrinkle.
  ~1-2 days total.
* **JuliaCall persistent path** (embed Julia in Python process).
  Faster than subprocess + stdin. Probably not worth it — the
  persistent subprocess path is fast enough and has zero build
  complexity. Defer unless someone hits a real perf ceiling.

### D-tier (polish / smaller)

* **Tool rename** — `"ModelicaTesting"` → neutral name. Three
  Modelica-adjacent backends + one Julia backend = the name is
  misleading now. (Resolved in D81: now DSTF / Dynamic Systems Testing
  Framework.)
* **#53 `check-openmodelica` / `check-julia`** CLI subcommands.
* **#54 OM FMU export** via `buildModelFMU` — pairs with MTK FMU
  export for symmetric cross-backend chains.
* **#47 time-array dedup** — bump embedded-sample cap 1000 → 2000.
* **Visual-regression Playwright screenshots**.

### E-tier (foundational)

* **Phase 9 dataset types** — `Events`, `Spectrum`, `Distribution`,
  `Scalars`, `Field`. Unlocks Fréchet (#23), spectral coherence (#24),
  pyfunnel x-tolerance (#25), ISO 18571 (#26).

---

## Starter prompt for the next session

> Resuming DSTF (then ModelicaTesting) at commit `9cd5468` on `main`. This session
> was a 9-phase marathon (D71 through D79) adding Julia/MTK as the
> fourth simulator backend, building `JuliaMtkTestingLib` as a second
> test-fixture library, refactoring the reporter-as-IDE to feature
> completeness through dominant-frequency declared-peaks with live JS
> FFT, wiring 14 cross-library companion overlays, and fixing a
> sequence of small bugs surfaced along the way.
>
> **State at HEAD**: 752 tests passing + 1 skipped (reference_fmus) +
> 0 regressions. Four production backends (Dymola, FMPy, OpenModelica,
> Julia/MTK). Two test libraries (ModelicaTestingLib × 10, JuliaMtk
> TestingLib × 7). Reporter-as-IDE feature-complete through
> dominant-frequency editor.
>
> **Read first**: `docs/SESSION_HANDOFF.md` (this file), D79 → D77 in
> `docs/decisions.md` (the Julia story), and the A-E tier ordering at
> the top of `docs/ideas.md`.
>
> **Default next move — pick one**:
>
> 1. **Phase 7 rule-based recommender** (B-tier, ~2-3 days skeleton).
>    Lowers onboarding. Bounded by D66's complexity budget. No ML.
>
> 2. **TRANSFORM upstream portability PR** (B-tier external, ~1 day).
>    46 `each`-modifier fixes. Lifts OM pass rate 72% → 85%.
>
> 3. **#45 Python-driven tests** (B-tier, ~2-3 days MVP). Natural
>    pairing with D77's subprocess+TestResult contract — Julia path is
>    basically #45's template with `julia` swapped for `python`.
>
> **Smaller alternatives**:
> * MTK FMU export (~1 day) — cross-backend chain symmetry.
> * Dyad validation (~½ day) — prove Dyad→MTK→runner path.
> * Tool rename (~½ day, naming-blocked).
>
> **Dev env prereqs** (if venv was recreated): `uv pip install -e
> ".[dev,fmpy,om]"` + pytest-playwright + chromium. Julia:
> https://julialang.org/downloads/ + `cd examples/julia/JuliaMtk
> TestingLib && julia --project=. -e 'using Pkg; Pkg.instantiate()'`.
> Watch venv drift (miniforge vs project .venv).
>
> **Out of scope unless explicitly adopted**: Phase 9 dataset types,
> ML, mid-stream refactor of the existing six modes or four backends.
