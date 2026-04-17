# CLAUDE.md

## Project Overview

**ModelicaTesting** (working name — expected to be renamed once the multi-backend abstraction stabilizes) is a Python framework for regression and unit testing of time-dependent system behavior.

**Current state**: a multi-backend regression harness. Discovers tests by scanning `.mo` files and/or reading `test_spec.json`, simulates via Dymola (Python interface by default, batch `.mos` fallback) or FMPy (prebuilt FMUs), and compares results through a composable MetricTree (NRMSE / tube / final-only / range leaves; and / or / k-of-n / warn combinators; multi-baseline via `"against"`). Reports via live dashboard + interactive Plotly HTML + JUnit XML. Reference files use a hybrid schema supporting multiple named baselines (primary + optional `experiment` / `analytical` / `dymola` / ...).

**Forward direction**: [docs/vision.md](docs/vision.md) lays out a six-layer plug-in architecture (Source → Discovery → Backend → Dataset → Metric → MetricTree) enabling additional backends (FMPy / OpenModelica / Julia / Simulink / data-file ingest), additional dataset types (events, spectra, distributions), and composable metrics (AND / OR / weighted / K-of-N). The current Dymola implementation is the first consumer of the abstraction, not its reference model. See [docs/architecture.md](docs/architecture.md) for the layer ↔ code mapping and [docs/extensibility.md](docs/extensibility.md) for plug-in contracts.

**Phase 1 status (complete)**: Foundation abstractions are in place — `Capability` / `DatasetType` enums declared on `DymolaRunner`; `VariableComparison` carries a `diagnostics` bag; `comparison/metric_tree.py` provides `MetricResult` + `And/Or/KOfN/WarnCombinator` + `implicit_and_tree()` (unit-tested, not yet wired into the pipeline); `Config.source_type` reserved; `examples/modelica/ModelicaTestingLib/` relocated; reference-file `Baseline` view supports a hybrid schema for named baselines (primary stays flat; additional baselines live under an optional `baselines` key). Zero runtime-behavior change. Decisions D44–D47 in [docs/decisions.md](docs/decisions.md).

**Phase 2 status (2.1–2.4 complete; 2.5 deferred)**: FMPy backend implemented as the second `SimulatorRunner`, exercising the Phase 1 capability abstraction. Reference-FMUs binaries fetched via `scripts/fetch_reference_fmus.py` (release-ZIP download, not submodule) into gitignored `examples/fmu/reference-fmus-binaries/`. `FmpyRunner` (`simulators/fmpy/runner.py`) simulates via `fmpy.simulate_fmu`, persists results to `<test_dir>/result.npz`, and reads back through the existing comparator contract. `test_spec.json` gained an optional `"fmu"` field; `BatchManifest.mat_file` (dead Dymola-specific code) removed from backend-agnostic base. **2.4 done**: end-to-end CLI works against `examples/fmu/testing.json` (BouncingBall, Dahlquist, VanDerPol) — `Config.__post_init__` and `discover_tests` skip Modelica package.mo lookup when `source_type == "fmu"`; `library_name` falls back to the config dir name. Baselines committed under `examples/fmu/ReferenceResults/FMPy/<os>/`. **2.5 deferred**: GitHub Actions CI until the repo goes public; full recipe + decisions in [docs/PHASE_2_5_CI_PLAN.md](docs/PHASE_2_5_CI_PLAN.md). Decisions D48–D50 in [docs/decisions.md](docs/decisions.md).

**Phase 3 status (complete)**: MetricTree wired end-to-end. `comparator.compare_test()` always builds a `MetricResult` tree and derives `TestComparison.passed` from its root. Default tree is the implicit flat-AND `implicit_and_tree(comparisons)` (behavior-preserving). Users author explicit trees via a new `"metrics"` block in `test_spec.json`: parsed by `comparison/tree_spec.py` into `LeafSpec` / `CombinatorSpec` with path-bearing validation, evaluated by `comparison/tree_eval.py` against sim + reference data. Combinators: `and`, `or`, `k-of-n` (with `k`), `warn` (single child). Leaf metrics: `nrmse`, `tube`, `final-only`, `range` (last one is signal-only — bounds from spec, not baseline — proves the leaf contract isn't NRMSE-shaped). Per-test HTML report renders user-authored trees via recursive Jinja; implicit trees stay suppressed (per-variable table already conveys them). Decisions D51–D53.

**Cleanup pass (complete)**: diagnostic variables stored as scalar summary (`final/min/max`) not full trajectory — kills spurious git diffs on every `--accept`; reporter's "NRMSE" column → mode-aware "Score" (`NRMSE 2.1e-16`, `max_viol 0`, `100% in tube`); backend-aware artifact lists (`DymolaRunner.artifact_files` vs. `FmpyRunner.artifact_files`); documented per-test `simulation.timeout` (honored by Dymola today, not FMPy). Also fixed a bug where `discover_tests` silently dropped `test_spec.simulation.timeout` and `test_spec.metrics` when a model had both a `UnitTests` component and a spec entry. Decisions D54–D55.

**Phase 4.A status (complete)**: Multi-baseline MetricTree leaves. Leaves select via `"against": "<name>"` (default `"primary"`). `comparator.compare_test` loads every named baseline from the reference file via `_extract_baselines(reference)` and threads them into the evaluator as `baselines: dict[str, BaselineView]`. New `ReferenceStore.add_named_baseline(model_id, name, ...)` helper for programmatic non-primary baseline authoring (primary stays owned by `store_reference`). Per-test HTML shows `against=<name>` on non-primary leaves. BouncingBall demo has a synthetic `experiment` baseline + a `warn`-wrapped leaf scoring against it. Test count: 253 → 309. Decision D56.

**Phase 4.D status (complete)**: Modelica-neutral rename sweep + bundled cleanup follow-ups. Hard break: `TestModel.mo_file` → `source_file`, `TestModel.package_path` → `source_package`, `Config.package_path` → `Config.source_path` (CLI `--source-path`, `testing.json` key `source_path`). Modelica-specific code paths (`mo_parser`, `find_package_dir`, `dymola/`, ...) keep their Modelica names — they're genuinely Modelica-only. Bundled into the same phase: FMPy now honors `test.timeout` / `config.timeout` via `concurrent.futures` (worker thread leaks on timeout — acceptable for sub-second FMUs since C-level FMU compute can't be force-killed in-process); FMPy emits `loading` / `simulating` phase labels for dashboard parity with Dymola's `translating / simulating / finalizing`. External `testing.json` consumers (TRANSFORM) need a one-line key rename. Test count unchanged: 309. Decisions D57–D58.

**Phase 5 / PTA status (complete)**: Pluggable in-source test annotations. The Modelica `.mo` scan was generalized into a `Recognizer` registry (`discovery/recognizer.py`); the previous hardcoded `UnitTests` + `experiment(...)` pattern is now `BundledModelicaUnitTestsRecognizer`, one of N registered. Users declare custom recognizers as JSON in `testing.json` (`"recognizers"` list) — `JsonRecognizer` (`discovery/json_recognizer.py`) handles both `component-instantiation` and `extends` matchers, with `parameter` / `constant` / `experiment-annotation` field sources. Discovery merges results by `model_id`; bundled registers first, user appends, last-writer-wins per field — additive default + explicit `disable_bundled` opt-out. `TestModel` gained richer-contract fields (`simulate_only`, `requested_fmu_export`, `requested_baselines`); `simulate_only` is wired end-to-end in the comparator (test passes iff sim succeeds; no per-variable comparison; the others are 4.B placeholders). Demo lives in `examples/modelica/ModelicaTestingLib`: new `Icons/Example.mo` + `Examples/SimulateOnlyTest.mo` exercised by a recognizer entry in the bundled `testing.json`. Decomposition: PTA.1 registry → PTA.2 JSON schema → PTA.3 wire-into-Config → PTA.4 richer fields → PTA.5 simulate_only end-to-end → PTA.6 demo → PTA.7 docs+D59. Test count: 309 → 358. Decision D59.

**Bundled phase: PTA follow-ups + 4.E + 4.C + 4.B + interactive HTML (complete)**: Five originally-separate moves landed in one session.
- **PTA follow-ups (D60)**: `paths_include`/`paths_exclude` per-recognizer (folder filter); `all-of`/`any-of` match composition (recursive `CompositeMatch`); `class-name-glob` match type; `annotation` field source (extracts from any Modelica annotation block, not just `experiment(...)`).
- **4.E weighted combinator (D61)**: `WeightedCombinator` joins and/or/k-of-n/warn — `sum(w_i * score_i) <` (or `>`) `threshold`. Direction-aware so it works for NRMSE-shaped (lower better) and tube-shaped (higher better) trees.
- **4.C leaf metrics (D62)**: `event-timing` (compare event instants via duplicate-time detection; `time_tolerance`); `dominant-frequency` (FFT peak comparison; `rel_tolerance`). Same `ComparisonMode` plumbing as `range` (D53).
- **4.B cross-backend (D63)**: `Capability.FMU_EXPORT` is now real; `DymolaWorker.export_fmu` uses `translateModelFMU`; `simulators/cross_backend.py::produce_dymola_via_fmpy_baseline` chains primary export → FMPy simulate → named baseline `"dymola-via-fmpy"`. CLI's `cmd_run` invokes the chain after primary `run_tests` for tests with `requested_baselines=["dymola-via-fmpy"]` (PTA.4 field). **Validation caveat**: real Dymola export requires Windows + Dymola FMI license; tests use mocked `DymolaInterface` for the export step (FMPy half is real).
- **Interactive HTML (D64)**: NRMSE-only column → mode-aware `score_display`; per-variable tolerance input shows `n/a (mode=...)` for non-NRMSE modes; JS `computePass` special-cases non-NRMSE modes to use the originally-computed `v.passed`. Surgical fix preserves NRMSE slider workflow.

Test count: 358 → 404. Decisions D60–D64.

**FMU-pathway scope + cross-backend experimental labeling (complete)**: D65. Grilled the D63 deferred-validation caveat and surfaced a broader concern: the cross-backend chain (`produce_dymola_via_fmpy_baseline`) assumes Dymola-exports-FMU + FMPy-default-simulates is a meaningful cross-check, which is only true for *autonomous* tests (no external inputs, no python-driver, CS-vs-ME-irrelevant). Same semantic gap exists on the primary FMPy path. Changes: (a) `simulators/cross_backend.py` module docstring flagged **EXPERIMENTAL** with scope limits spelled out; one-time warning emitted by `cli._run_cross_backend_chains` when any chain fires; (b) `simulators/fmpy/runner.py` gained a **Limitations** block documenting what the current `simulate_fmu` call does *not* support (`input=` / `fmi_type=` / `start_values=` / python-driver tests) — scope-labeling only, *not* a Phase 2 status reversal (reference FMUs remain validated); (c) new `scripts/smoke_test_dymola_export.py` — standalone script the user ran on Windows to validate `translateModelFMU` signature + FMI license + cwd-on-Windows, independently of the test suite. **Smoke test PASSED on Dymola 2026x (2026-04-17)** against `Modelica.Blocks.Examples.PID_Controller` — signature/license/cwd dimensions of the validation caveat are now locked. Full chain (export → FMPy-simulate → baseline write) still unvalidated on real Dymola; semantic-gap generalization (input drivers, CS/ME choice, start-value overrides, python-driver tests) bundled into a future "FMU-path semantic gap closure" phase. No runtime-behavior change beyond the warning. Test count unchanged: 404. Decision D65.

## Project Structure

```
ModelicaTesting/
├── src/
│   └── modelica_testing/        # Python package (src layout)
│       ├── cli.py               # CLI: discover, run, compare, export, manifest, add, spec-update
│       ├── config.py            # Config dataclass, path resolution, testing.json loading
│       ├── discovery/           # Test discovery: scan .mo for UnitTests, parse test_spec.json
│       ├── simulators/          # Abstract runner + Dymola backend (batch .mos, .mat reader, dslog parser)
│       ├── comparison/          # NRMSE and tube comparison with piecewise event handling
│       ├── storage/             # JSON reference storage with in-memory index
│       └── reporting/           # Console, JUnit XML, HTML reporters, plot generation
│           └── templates/       # Jinja2 templates (comparison.html, interactive.html) + comparison_data.json sidecar
├── examples/
│   └── modelica/
│       └── ModelicaTestingLib/  # Modelica library: UnitTests component + example models
│   ├── Components/UnitTests.mo  # Reusable UnitTests component for tracking variables
│   ├── Examples/                # SimpleTest, EventTest, ConstantTest, IntervalTest, NoUnitTest
│   └── Resources/ReferenceResults/  # testing.json + reference baselines for this library
├── tests/                       # pytest test suite (174 tests)
│   ├── fixtures/                # Test data: dslog.txt, .mat file, test_spec.json
│   └── test_*.py                # Comparator, config, discovery, storage, simulators
├── docs/                        # Design decisions, patterns, architecture, constraints, usage
├── pyproject.toml               # uv/hatchling project config
└── CLAUDE.md
```

## Running the Tool

The package ships a console script (`[project.scripts]` in `pyproject.toml`). The canonical dev invocation is `uv run modelica-testing ...`. `python -m modelica_testing ...` is supported as a fallback (both call `cli.main_entry`). End users install via `uv tool install modelica-testing` (or `pipx install`) and run plain `modelica-testing`.

```bash
# With testing.json containing source_path — single entry point
uv run modelica-testing --config path/to/testing.json run

# Or with explicit flags
uv run modelica-testing --source-path /path/to/MyLib --reference-root /path/to/refs run

# Interactive review (accept/skip/plot per test)
uv run modelica-testing --config testing.json run -i

# Interactive review filtered to specific categories
uv run modelica-testing --config testing.json run -i failed
uv run modelica-testing --config testing.json run -i no-baseline
# Categories: failed, no-baseline, warnings, sim-failed, passed, all

# Accept all results as new baselines
uv run modelica-testing --config testing.json run --accept

# Generate HTML report with per-test plots (static + interactive Plotly)
uv run modelica-testing --config testing.json run --report ./reports

# Parallel run with small-batch queue dispatch (better load balancing + crash isolation)
uv run modelica-testing --config testing.json run --parallel 4 --batch-size 3
# Live progress: open work_dir/dashboard.html (auto-refreshes every 2s; URL printed on start)

# Filter accepts: glob, comma-separated list, or @file (one pattern per line, # comments)
uv run modelica-testing --config testing.json run --filter "Foo.A,Foo.B"
uv run modelica-testing --config testing.json run --filter @rerun.txt

# Incremental rerun + full merged report (rerun a subset, report covers everything)
uv run modelica-testing --config testing.json run --filter @failed.txt --merge --report

# Auto-rerun previously failed tests (implies --merge)
uv run modelica-testing --config testing.json run --rerun --report
# Or pick categories: failed, no-baseline, warnings, sim-failed, passed
uv run modelica-testing --config testing.json run --rerun failed,sim-failed --report

# Compare without re-running simulations (uses last results)
uv run modelica-testing --config testing.json compare

# Apply tolerance config exported from interactive report to test_spec.json
uv run modelica-testing --config testing.json spec-update tolerance_config.json

# Dump reference manifest (ref ID to model name mapping) without running tests
uv run modelica-testing --config testing.json manifest dump

# Prune orphan manifest entries (models no longer in discovery — dry-run by default)
uv run modelica-testing --config testing.json manifest cleanup --orphans
uv run modelica-testing --config testing.json manifest cleanup --orphans --apply

# Persistent workers via the Dymola Python interface are the DEFAULT.
# Live per-test dashboard (with phase: translating / simulating / finalizing),
# natural load balancing, per-test timeout watchdog with disk-check rescue,
# per-phase timing in per-test report ("Timing" section) and on index (sortable columns).
uv run modelica-testing --config testing.json run --parallel 4 --report

# Force the legacy batched .mos runner (e.g., for environments without the Python interface)
uv run modelica-testing --config testing.json run --batch --parallel 4 --batch-size 3 --report

# Diagnose discovery of the Dymola Python interface archive
uv run modelica-testing check-dymola
```

## Running Tests

```bash
uv pip install -e ".[dev]"    # One-time: install package + pytest
uv run pytest                  # Run the test suite
```

## Configuration

The tool looks for `testing.json` near the library root or reference root. Key fields:

- `source_path` — path to library's source location (Modelica package.mo directory, FMU directory, ...; relative to testing.json)
- `simulator` — named entry like `"Dymola"` or `"Dymola 2025"`
- `simulators` — map of simulator names to candidate executable paths
- `simulator_setup` — list of Modelica commands run after library loading (user-specific settings)
- `dependencies` — paths to dependency library roots loaded before simulation
- `reference_root` — where reference results live (default: `<repo>/Resources/ReferenceResults`)
- `test_spec` — path to external test definitions file
- `tolerance` — global NRMSE comparison tolerance (default: `1e-4`)
- `diagnostic_variables` — variables auto-captured but not compared (default: `["CPUtime", "EventCounter"]`)

Note: `OutputCPUtime := true;` and `Advanced.UI.TranslationInCommandLog := true;` are hardcoded in the Dymola runner — no need to add them to `simulator_setup`.

Reference results are partitioned by `<reference_root>/<SimulatorBackend>/<os>/`.

### test_spec.json format

Simulation parameters live under a `simulation` key, comparison settings under a `comparison` key. Both are optional — minimal entries need only `model` and `variables`:

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

**NRMSE** (default): `NRMSE = RMSE / signal_range`. Pass if below tolerance.

**Tube**: envelope around the reference trajectory. Configured per-variable via `variable_overrides` with `"mode": "tube"`. Three width modes controlled by `tube_width_mode`:
- `"rel"` (default in interactive UI): width = fraction of |reference| (e.g., `"tube_rel": 0.02` = 2%)
- `"band"` (or legacy `"abs"`): width = offset in signal units (e.g., `"tube_abs": 500`)
- `"absolute"`: upper/lower are literal y-axis values (not offsets from reference)

Legacy format (no `tube_width_mode`): width = `max(tube_abs, tube_rel * |reference|)`. Pass if every point stays inside the tube. Supports time-varying tubes via `tube_points` with linear or stepwise interpolation.

### Tolerance resolution order

Per-variable override (spec) > per-variable override (reference JSON) > per-test comparison tolerance > reference JSON comparison tolerance > config.tolerance > default (1e-4). When accepting results, comparison settings are saved in the reference JSON's `comparison` section so tolerances travel with the baseline.


## Key Abstractions

- **`Config`** (`config.py`) — resolves all paths from CLI args + `testing.json` + defaults; passed to runners and reporters but not to comparison functions
- **`TestModel`** (`discovery/test_registry.py`) — fully resolved test with model ID, simulation params, tracked variables, source
- **`SimulatorRunner`** (`simulators/base.py`) — abstract interface; backends self-register via `@register` decorator; `get_runner(config)` factory in `simulators/__init__.py`
- **`DymolaRunner`** (`simulators/dymola/runner.py`) — batch execution backend; `DymolaConfig` dataclass extracts Dymola-specific settings from Config
- **`ReferenceStore`** (`storage/reference_store.py`) — CRUD for per-test JSON reference files; `RefIndex` built in-memory from scanning ref files
- **`ComparisonMode`** (`comparison/modes.py`) — strategy pattern for variable comparison: `NrmseMode`, `TubeMode`, `FinalOnlyMode` with typed config dataclasses; `resolve_mode()` factory builds mode from per-variable override dict
- **`comparator`** (`comparison/comparator.py`) — orchestrates per-test comparison; `compare_test()` takes `default_tolerance` and `final_only` (not Config); delegates per-variable comparison to `ComparisonMode` strategies

## Design Principles

1. **Library-agnostic**: auto-detects library name from `package.mo`, all paths configurable
2. **Simulator-agnostic**: Dymola-specific code isolated in `simulators/dymola/`; abstract `SimulatorRunner` interface with registry pattern
3. **Stable test IDs**: numeric IDs (`ref_0001.json`) with model ID inside each file; IDs never reused; in-memory index built by scanning ref files (no persistent manifest)
4. **Reference partitioning**: results split by simulator backend and OS since solvers produce platform-specific results
5. **Batch execution**: load libraries once per worker, run N tests, exit — avoids per-test startup overhead
6. **No backward compatibility**: clean breaks during development; migration utilities provided for format changes
7. **Strategy over conditionals**: comparison modes and simulator backends use strategy/registry patterns instead of if/elif dispatch
