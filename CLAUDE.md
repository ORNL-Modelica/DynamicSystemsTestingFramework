# CLAUDE.md

## Project Overview

**Dynamic Systems Testing Framework (DSTF)** — formerly **ModelicaTesting** — is a Python framework for regression and unit testing of time-dependent system behavior across simulated and pre-recorded trajectories.

**Current state**: a multi-backend regression harness. Discovers tests by scanning `.mo`/`.jl` files and/or reading `test_spec.json`; simulates via **Dymola** (Python interface default, batch `.mos` fallback), **FMPy** (prebuilt FMUs), **OpenModelica** (OMPython persistent-worker default, `omc` batch fallback), **Julia/ModelingToolkit** (persistent `julia` worker), or **Python** (subprocess-per-test; any `simulate(stop_time, tolerance) -> dict` — scipy, pandas, CSV, HTTP, etc.); scores via a composable **MetricTree** (leaves: `nrmse` / `tube` / `points` / `range` / `event-timing` / `dominant-frequency`; combinators: `and` / `or` / `k-of-n` / `warn` / `weighted`; multi-baseline via `"against"`; time-windowed leaves via `"window"`). Reports via live dashboard + **interactive Plotly HTML** (Phase 6 reporter-as-IDE: per-leaf live scoring, structural tree editing, Shift+click/drag tube/range/peak editors, RFC 6902 JSON-Patch export round-tripped by `spec-update`) + JUnit XML. References partitioned under `<reference_root>/<Backend>/<os>/` with three baseline roles: **primary** (regression anchor, hard-fail), **soft_checks** (warn-wrapped cross-regression imports), **companions** (plot-only overlays, including cross-library).

**Forward direction**: [docs/vision.md](docs/vision.md) lays out the six-layer plug-in architecture (Source → Discovery → Backend → Dataset → Metric → MetricTree). See [docs/architecture.md](docs/architecture.md) for the layer ↔ code mapping and [docs/extensibility.md](docs/extensibility.md) for plug-in contracts.

**Project history**: [docs/decisions.md](docs/decisions.md) is the authoritative log (D44–D79 covers the work summarized above: Phase 1 abstractions, Phase 2 FMPy, Phase 3 MetricTree, Phase 4 multi-baseline + cross-backend + weighted, Phase 5 PTA recognizers, Phase 6 reporter-as-IDE MVP + baseline-role split, D69–D70 OpenModelica, D77–D79 Julia/MTK, D80 Python-driven tests, D81 rename to DSTF). Do not re-summarize past phases in this file — grep `decisions.md` or `git log` when context is needed.

## Project Structure

```
ModelicaTesting/
├── src/dstf/                     # Python package (src layout)
│   ├── cli.py                    # CLI: run, compare, discover, manifest, spec-update,
│   │                             #      companion, soft-check, import-baseline, migrate-baselines, export-schema
│   ├── config.py                 # Config dataclass, path resolution, testing.json loading, auto-detect simulator
│   ├── discovery/                # Recognizer registry, JSON recognizer, test_spec parse, RFC 6902 patch_apply
│   ├── simulators/               # Abstract runner + Dymola / FMPy / OpenModelica / Julia backends
│   │   ├── common/               # Shared: mat_reader, persistent-worker dispatch scaffolding
│   │   └── cross_backend.py      # EXPERIMENTAL Dymola→FMU→FMPy baseline chain
│   ├── comparison/               # ComparisonMode registry, MetricTree spec/eval, validator, tree_spec helpers
│   ├── storage/                  # ReferenceStore (primary + soft_checks + companions), RefIndex
│   └── reporting/                # Console + JUnit + HTML (interactive.html + interactive.js + comparison_data.json)
│       ├── ui/                   # mode_controls auto-derivation, window controls, schema export
│       └── templates/            # Jinja2 templates (interactive.html)
├── examples/
│   ├── modelica/ModelicaTestingLib/   # Demo Modelica library (UnitTests component + 10+ showcase tests)
│   ├── fmu/                           # Reference-FMUs demo (BouncingBall / Dahlquist / VanDerPol)
│   └── julia/JuliaMtkTestingLib/      # Julia/MTK demo library (mirrors ModelicaTestingLib layout)
├── tests/                        # pytest suite (~750 tests; Playwright subset gated by importorskip)
├── docs/                         # decisions.md, vision.md, architecture.md, qa/reporter_checklist.md, ideas.md
├── scripts/                      # fetch_reference_fmus.py, smoke_test_dymola_export.py
├── pyproject.toml                # uv/hatchling config; optional extras: [om], [julia]
└── CLAUDE.md
```

## Running the Tool

The package ships a console script (`[project.scripts]` in `pyproject.toml`). The canonical dev invocation is `uv run dstf ...`. `python -m dstf ...` is supported as a fallback (both call `cli.main_entry`). End users install via `uv tool install dstf` (or `pipx install`) and run plain `dstf`.

```bash
# With testing.json containing source_path — single entry point
uv run dstf --config path/to/testing.json run

# Or with explicit flags
uv run dstf --source-path /path/to/MyLib --reference-root /path/to/refs run

# Interactive review (accept/skip/plot per test)
uv run dstf --config testing.json run -i
uv run dstf --config testing.json run -i failed
# Categories: failed, no-baseline, warnings, sim-failed, passed, all

# Accept all results as new baselines
uv run dstf --config testing.json run --accept

# Generate HTML report with per-test plots (interactive Plotly)
uv run dstf --config testing.json run --report ./reports

# Parallel run with small-batch queue dispatch
uv run dstf --config testing.json run --parallel 4 --batch-size 3
# Live progress: open work_dir/dashboard.html (auto-refreshes every 2s; URL printed on start)

# Filter accepts: glob, comma-separated list, or @file (one pattern per line, # comments)
uv run dstf --config testing.json run --filter "Foo.A,Foo.B"
uv run dstf --config testing.json run --filter @rerun.txt

# Incremental rerun + full merged report
uv run dstf --config testing.json run --filter @failed.txt --merge --report
uv run dstf --config testing.json run --rerun failed,sim-failed --report

# Compare without re-running simulations (uses last results)
uv run dstf --config testing.json compare

# Apply a JSON-Patch (or legacy tolerance dict) exported from the interactive report
uv run dstf --config testing.json spec-update spec_patch.json

# Baseline-role CLIs
uv run dstf --config testing.json companion add <model> <name> <path>
uv run dstf --config testing.json soft-check list <model>
uv run dstf --config testing.json import-baseline <model> <role> <name> <source>

# Manifest / schema
uv run dstf --config testing.json manifest dump
uv run dstf --config testing.json manifest cleanup --orphans [--apply]
uv run dstf --config testing.json export-schema --output schema.json

# Persistent workers are the DEFAULT for Dymola and OpenModelica and Julia.
# Force legacy batched runner (.mos / omc -s / per-test julia subprocess)
uv run dstf --config testing.json run --batch --parallel 4 --report

# Diagnose Dymola Python interface discovery
uv run dstf check-dymola
```

## Running Tests

```bash
uv pip install -e ".[dev]"        # One-time: install package + pytest
uv run pytest                      # Run the suite
uv run pytest -m "not playwright"  # Skip browser-driven interactive-HTML tests
```

## Configuration

The tool looks for `testing.json` near the library root or reference root. Key fields:

- `source_path` — path to the library's source location (Modelica package.mo directory, FMU directory, Julia project, ...; relative to testing.json)
- `source_type` — `"modelica"` (default) / `"fmu"` / `"julia"` (usually auto-detected)
- `simulator` — named entry like `"Dymola"` or `"OpenModelica"`; omit to auto-detect from the `simulators` map
- `simulators` — map of simulator names to candidate executable paths
- `simulator_setup` — list of Modelica commands run after library loading (user-specific settings)
- `dependencies` — library names (e.g. `"Modelica"`) or paths to dependency roots loaded before simulation
- `reference_root` — where reference results live (default: `<repo>/Resources/ReferenceResults`)
- `test_spec` — path to external test definitions file
- `recognizers` — custom JSON recognizers appended to the bundled list (see `discovery/json_recognizer.py`)
- `tolerance` — global NRMSE comparison tolerance (default: `1e-4`)
- `max_embedded_samples` — LTTB decimation cap per variable in `interactive.html` (default: 1000)
- `diagnostic_variables` — variables auto-captured but not compared (default: `["CPUtime", "EventCounter"]`)

Note: `OutputCPUtime := true;` and `Advanced.UI.TranslationInCommandLog := true;` are hardcoded in the Dymola runner.

Reference results are partitioned by `<reference_root>/<SimulatorBackend>/<os>/`. Soft-checks live under `.../soft_checks/ref_NNNN/<name>.json`, companions under `.../companions/ref_NNNN/<name>.{json,csv}`.

### test_spec.json format

Simulation parameters live under `simulation`, comparison settings under `comparison`, optional explicit scoring tree under `metrics`. Minimal entries need only `model` and `variables`:

```json
{
  "tests": [
    {
      "model": "MyLib.Examples.SimpleTest",
      "variables": ["pipe.T[1]", "pipe.m_flow"],
      "simulation": {"stop_time": 100, "tolerance": 1e-6},
      "comparison": {
        "tolerance": 0.01,
        "variable_overrides": {
          "pipe.T[1]": {"tolerance": 0.1},
          "pipe.p[1]": {"mode": "tube", "tube_width_mode": "rel", "tube_rel": 0.02}
        }
      }
    }
  ]
}
```

### Comparison modes

- **`nrmse`** (default) — `RMSE / signal_range`; pass if below tolerance.
- **`tube`** — envelope around the reference trajectory. `tube_width_mode`: `"rel"` (fraction of |ref|), `"band"` (offset in signal units), `"absolute"` (literal y-bounds). Supports time-varying control points with linear or stepwise interpolation.
- **`points`** — declared-checkpoint comparison. Empty list ⇒ final-value-only check (legacy behavior). Non-empty list ⇒ multi-point with per-point abs/rel y-tolerance and symmetric `time_tolerance` (x-tolerance) box check. Each point may set an explicit `value` (baseline-free) or fall back to `ref(time)`.
- **`range`** — scalar `min_value` / `max_value` bounds on the signal; baseline-free (bounds come from the spec).
- **`event-timing`** — compares event instants via Modelica duplicate-time detection; `time_tolerance` config; CLI-authoritative.
- **`dominant-frequency`** — FFT peak matching against a declared-peaks table (`peaks: [{freq, tolerance, tolerance_mode}, ...]`); live JS scorer with windowed re-FFT and per-peak provenance.

Leaves can be wrapped in an explicit `metrics` tree with combinators (`and`, `or`, `k-of-n`, `warn`, `weighted`) and scoped to a time window (`"window": {"start": ..., "end": ...}`).

### Tolerance resolution order

Per-variable override (spec) > per-variable override (reference JSON) > per-test comparison tolerance > reference JSON comparison tolerance > config.tolerance > default (1e-4). When accepting results, comparison settings are saved in the reference JSON's `comparison` section so tolerances travel with the baseline.

## Key Abstractions

- **`Config`** (`config.py`) — resolves all paths from CLI args + `testing.json` + defaults; passed to runners and reporters but not to comparison functions.
- **`TestModel`** (`discovery/test_registry.py`) — fully resolved test: model ID, simulation params, tracked variables, source, `simulate_only`, `requested_baselines`, `requested_fmu_export`.
- **`Recognizer`** (`discovery/recognizer.py`) — registry of source-scan rules; bundled + user-declared JSON recognizers merged by `model_id`.
- **`SimulatorRunner`** (`simulators/base.py`) — abstract interface; backends self-register via `@register` and declare `Capability` flags (`BATCH_FALLBACK`, `FMU_EXPORT`, ...). Concrete backends: `DymolaRunner` / `PersistentDymolaRunner`, `FmpyRunner`, `OpenModelicaRunner` / `PersistentOpenModelicaRunner`, `JuliaRunner` / `PersistentJuliaRunner`.
- **`Worker`** + **`PersistentRunnerBase`** (`simulators/base.py`) — `Worker` ABC declares the long-lived-process contract every persistent backend must satisfy (`start` / `close` / `is_alive` / `run_test_with_timeout`). `PersistentRunnerBase` is a template-method class owning the worker-pool orchestration (parallel startup, shared queue + sentinel dispatch, per-worker restart on death). New persistent backends supply a `Worker` subclass + a small set of hooks (`worker_cls`, `backend_label`, `make_worker`, `setup_before_workers`, `preflight`) — see [docs/extensibility.md](docs/extensibility.md) §3 "Persistent-worker contract".
- **`ReferenceStore`** (`storage/reference_store.py`) — CRUD for primary refs + soft_checks + companions; `RefIndex` built in-memory from scanning.
- **`ComparisonMode`** (`comparison/modes.py`) — strategy pattern for per-variable comparison with typed config dataclasses; field metadata (`label` / `help` / `ui_min` / `ui_max`) flows into auto-derived UI via `reporting/ui/mode_controls.py`.
- **`MetricTree`** (`comparison/tree_spec.py` + `tree_eval.py`) — `LeafSpec` / `CombinatorSpec` + evaluator; `implicit_and_tree()` for specs without an explicit tree; `validator.py` enforces baseline-role rules (≥ 1 primary leaf outside warn; soft_checks warn-wrapped; companions not targetable).
- **`comparator`** (`comparison/comparator.py`) — orchestrates per-test comparison; threads primary + named baselines into the evaluator; derives `TestComparison.passed` from the tree root.

## Design Principles

1. **Library-agnostic**: auto-detects library name from `package.mo` / project layout, all paths configurable.
2. **Simulator-agnostic**: backend-specific code isolated under `simulators/<name>/`; abstract interface with registry + capability flags.
3. **Stable test IDs**: numeric IDs (`ref_0001.json`) with model ID inside each file; IDs never reused; in-memory index built by scanning.
4. **Reference partitioning**: results split by simulator backend and OS since solvers produce platform-specific results.
5. **Batch / persistent execution**: load libraries once per worker, run N tests, exit — avoids per-test startup overhead.
6. **No backward compatibility**: clean breaks during development; migration utilities provided for format changes.
7. **Strategy over conditionals**: modes, backends, recognizers, and UI renderers all use registry + strategy patterns, not if/elif dispatch.
8. **Economy of tools** (D66): the tool does regression testing and emits handoff-ready artifacts (JSON-Patch, JSON-Schema, baselines); calibration / RCA / parameter-estimation / ML-ranking belong in downstream tools that consume our outputs.
