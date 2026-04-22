# OpenModelica backend — design spec

**Date**: 2026-04-22
**Status**: design approved, ready for implementation plan
**Owner**: ModelicaTesting / Phase 5-adjacent work (new backend, not in any numbered phase)
**Related decisions**: D44–D50 (multi-backend abstraction), D57 (Modelica-neutral rename), D65 (FMU-path scope caveats)

## Summary

Add **OpenModelica** as a third `SimulatorRunner` alongside Dymola and FMPy. MVP path uses `omc` as a subprocess driven by generated `.mos` scripts (analogous to Dymola's batch fallback — *not* persistent workers). Goal: run `examples/modelica/ModelicaTestingLib/` end-to-end on Linux using OpenModelica, producing fresh baselines under `Resources/ReferenceResults/OpenModelica/linux/`. First real exercise of the multi-backend abstraction against Modelica source (FMPy is FMU-sourced, not Modelica-sourced).

## Motivation

- The `SimulatorRunner` abstraction has existed since Phase 1, but no Modelica source → non-Dymola simulator path has ever run. FMPy consumes prebuilt `.fmu` binaries, not `.mo` files.
- Day-to-day dev against `ModelicaTestingLib` currently requires Windows + Dymola. Adding a Linux-native Modelica simulator removes that bottleneck for the primary dev machine.
- OpenModelica is free, installed via `apt`, and ships `omc` (batch compiler) + `OMEdit` (GUI). User has both installed (`omc 1.26.3`).

## Verified facts (from smoke test, 2026-04-22)

1. `omc <script.mos>` is a self-contained subprocess entry point; no shell or OMEdit required.
2. Default output format is `.mat` — **same DSresult family as Dymola**. Verified: `src/modelica_testing/simulators/dymola/mat_reader.py::read_dymola_mat()` reads OpenModelica's `{Model}_res.mat` unchanged (136 variables resolved correctly for `Modelica.Blocks.Examples.PID_Controller`).
3. `simulate(...)` in `.mos` returns a `record SimulationResult` containing per-phase timings (`timeFrontend`, `timeBackend`, `timeSimCode`, `timeTemplates`, `timeCompile`, `timeSimulation`, `timeTotal`), `resultFile`, and `messages`. Captured via stdout.
4. `{Model}_info.json` is a **structural dump** (variables / equations / functions) — does **not** contain phase timings. Timings come from the stdout record only.
5. MSL is not bundled; user must run `updatePackageIndex(); installPackage(Modelica);` once per machine (one-time; populates `~/.openmodelica/libraries/`).

## Architecture

### New package layout

```
src/modelica_testing/simulators/
├── common/
│   └── mat_reader.py                  # HOISTED from dymola/ — shared DSresult MAT reader
├── dymola/                             # unchanged modulo mat_reader import path
└── openmodelica/
    ├── __init__.py                    # re-exports OpenModelicaConfig, OpenModelicaRunner
    ├── runner.py                      # OpenModelicaRunner(SimulatorRunner) @ register("OpenModelica")
    ├── mos_generator.py               # pure-text .mos builders
    └── log_parser.py                  # parse omc stdout SimulationResult record + error strings
```

### Shared-code hoist

Move `src/modelica_testing/simulators/dymola/mat_reader.py` to `src/modelica_testing/simulators/common/mat_reader.py`. Rename exported function `read_dymola_mat` → `read_result_mat` (format is not Dymola-specific; OpenModelica uses the same format deliberately, for interoperability). Update Dymola imports. Mechanical refactor, zero behavior change. Baseline test count (637) must stay green after this step.

### OpenModelicaConfig

Frozen dataclass mirroring `DymolaConfig`:

```python
@dataclass(frozen=True)
class OpenModelicaConfig:
    omc_path: str                       # resolved executable; default = shutil.which("omc")
    simulator_setup: tuple[str, ...]    # .mos commands run after loading libraries
    diagnostic_variables: tuple[str, ...]
    std_version: str = "latest"         # setCommandLineOptions("--std=...")

    @classmethod
    def from_config(cls, config: Config) -> "OpenModelicaConfig": ...
```

No new universal `Config` fields required. Backend-specific knobs (e.g., `std_version`) are carved out of the universal Config at backend-bind time, same pattern as `DymolaConfig`.

### OpenModelicaRunner declarations

```python
@register("OpenModelica")
class OpenModelicaRunner(SimulatorRunner):
    capabilities = frozenset({Capability.BATCH_FALLBACK})
    produced_datasets = frozenset({DatasetType.TIME_SERIES})
    artifact_files = (
        ("simulate.mos", "Simulation script"),
        ("result_res.mat", "Result file"),
        ("result.log", "Simulation log"),
        ("result_info.json", "Model info"),
        ("omc_stdout.txt", "omc output"),
    )
```

Static filenames are achieved by passing `fileNamePrefix="result"` to `simulate(...)` in the generated `.mos`. Otherwise OM prefixes every output with the fully-qualified model name (e.g., `Modelica.Blocks.Examples.PID_Controller_res.mat`), which would force the base-class `artifact_files` contract to support templates. Aligning with the Dymola pattern (fixed names) is cleaner.

- **No** `PERSISTENT_WORKERS` (deferred to follow-up via OMPython / OMCSessionZMQ).
- **No** `FMU_EXPORT` (deferred; `buildModelFMU` wiring is a future cross-backend enhancement).
- Compile intermediates (`.c`, `.o`, `.h`, `.bin`, native executable) stay on disk but are not exposed via the report.

### Registry wiring

`src/modelica_testing/simulators/__init__.py::_import_builtin_backend` gains one entry:

```python
"OpenModelica": ".openmodelica",
```

`config.py::SIMULATOR_BACKENDS` already has `"OpenModelica": "OpenModelica"` (line 29); no changes needed there.

## Run path

Per-test working directory: `<work_dir>/<test_key>/`, owns the `.mos`, the `_res.mat`, the compile artifacts, and `omc_stdout.txt`.

### Generated `.mos` shape

```mos
setCommandLineOptions("--std=<std_version>");
loadModel(Modelica);                              // if "Modelica" listed in dependencies
getErrorString();
loadFile("<abs path to dep1/package.mo>");        // one per Config.dependencies path
getErrorString();
loadFile("<abs path to library package.mo>");
getErrorString();

<simulator_setup line 1>;                         // from OpenModelicaConfig.simulator_setup
<simulator_setup line 2>;

cd("<test_dir>");                                 // so result_res.mat lands in per-test dir
res := simulate(<model_id>,
    startTime=<start>, stopTime=<stop>,
    numberOfIntervals=<N>, tolerance=<tol>,
    method="<method>",
    outputFormat="mat",
    fileNamePrefix="result",
    variableFilter="<regex built from tracked vars + time + diagnostics>");
getErrorString();
print("<<<MT_PHASE_TIMINGS>>>");
print("timeFrontend=" + String(res.timeFrontend));
print("timeBackend=" + String(res.timeBackend));
print("timeSimCode=" + String(res.timeSimCode));
print("timeTemplates=" + String(res.timeTemplates));
print("timeCompile=" + String(res.timeCompile));
print("timeSimulation=" + String(res.timeSimulation));
print("timeTotal=" + String(res.timeTotal));
print("resultFile=" + res.resultFile);
print("messages=" + res.messages);
print("<<<MT_PHASE_TIMINGS_END>>>");
```

Fenced sentinel block (`<<<MT_PHASE_TIMINGS>>>` … `<<<MT_PHASE_TIMINGS_END>>>`) bounds the region `log_parser.py` parses. No regex on unbounded omc output.

### Dependency handling

`Config.dependencies` entries are classified:
- **Path-like** (contains `/` or `\`, ends in `.mo`, or resolves to an existing file): emit `loadFile("<absolute path>")`.
- **Bare library name** (e.g., `"Modelica"`): emit `loadModel(<Name>)` — resolves via OM's installed-packages store under `~/.openmodelica/libraries/`.

MVP assumes MSL is pre-installed on the machine. `runner.py` module docstring documents the one-time bootstrap:

```bash
omc -e 'updatePackageIndex(); installPackage(Modelica); getErrorString();'
```

Auto-install is out of scope. `loadModel(Modelica)` failure surfaces as a translation error with a clear "MSL not installed; run `installPackage(Modelica)`" hint in `error_message`.

### Subprocess invocation

```python
subprocess.run(
    [config.omc_path, "simulate.mos"],
    cwd=test_dir,
    capture_output=True,
    text=True,
    timeout=test.timeout or config.timeout,
)
```

Full stdout → `omc_stdout.txt` artifact. On `TimeoutExpired`, kill the process tree (omc is a standalone executable — no C-level compute in-process as in FMPy, so hard-kill is safe and instant).

### Failure classification

- Nonzero exit **or** nonempty `getErrorString()` blocks **or** empty `res.resultFile` in the parsed sentinel block ⇒ failure.
- `error_message` = combined `getErrorString()` output + `res.messages`, truncated to ~2KB.
- `TimeoutExpired` ⇒ `timed_out=True`, elapsed = configured timeout.

### Phase label emission (dashboard)

Single-subprocess constraint means we can't stream sub-phases. Emit:
- `on_start(test_key, worker_id)` before subprocess launch.
- `on_phase(test_key, "translating")` immediately after start — coarse, matches Dymola's batch-fallback posture.
- `on_phase(test_key, "simulating")` when parsed `resultFile` is nonempty (post-parse, before `on_finish`).
- `on_finish(test_key, success, elapsed, detail, timed_out)` after result read.

Persistent-worker follow-up will split these properly (OMPython lets us observe individual `buildModel`/`simulate` calls).

## Result reading

`read_result(test, test_key, run_result) → TestResult`:

1. **Assert `result_res.mat` exists**. Missing ⇒ failure, error lifted from `omc_stdout.txt`.
2. **Resolve tracked variables** via existing `_compute_needed_variables()` logic — test.variable_patterns against MAT variable list.
3. **Read MAT** via hoisted `simulators.common.mat_reader.read_result_mat()`.
4. **Build `VariableResult` list**: one per tracked variable. Diagnostic variables (`CPUtime`, `EventCounter`) stored as scalar summaries per D54–D55 (not full trajectories).
5. **Statistics**: phase timings from `log_parser.parse_timings(omc_stdout.txt)` → `statistics.timing = {"frontend", "backend", "simcode", "templates", "compile", "simulation", "total"}`. Populate `run_result.translation_wall = frontend + backend + simcode + templates + compile` and `run_result.sim_wall = simulation` — dashboard `Timing` section renders for free.
6. **Error message**: failure cases already populated upstream in `run_single_test`; `read_result` passes through.

### OpenModelica-specific quirk

`simulate(..., variableFilter="<regex>")` — OM accepts a regex filter over variable names. Build from escaped tracked-variable names joined with `|`, always including `^time$`, always including any declared diagnostic-variable patterns. Significantly shrinks the `.mat` vs the `.*` default (OM dumps all parameters, aliases, derivatives otherwise). Without this, a modest `ModelicaTestingLib` test can balloon to tens of thousands of variables.

## Testing strategy

### Pure unit tests (always run; no subprocess)

- **`tests/test_openmodelica_mos.py`** (~8 tests): `.mos` text generation across fixtures.
  - startup with / without dependencies
  - `loadModel` vs `loadFile` classification of dependency entries
  - per-test with and without variable filter
  - sentinel timing block present
  - `simulator_setup` lines inserted between loading and `cd`
  - absolute path normalization for `loadFile` / `cd`
  - model-id escaping (dots preserved; special chars handled)
  - empty dependencies list

- **`tests/test_openmodelica_log_parser.py`** (~6 tests): parse fixture stdout.
  - success record populated
  - failure record (empty `resultFile`)
  - error-string after load failure
  - malformed / truncated sentinel block ⇒ graceful failure with clear message
  - timeout (no sentinel block at all) ⇒ returns `None` for timings, not a crash
  - multiple `getErrorString()` notifications stitched

### Integration tests (real `omc`; gated via `shutil.which("omc") is None` skip)

- **`tests/test_openmodelica_runner.py`** (~4 tests):
  1. `run_single_test` + `read_result` round-trip on `Modelica.Blocks.Examples.PID_Controller` (MSL-only, isolates MSL bootstrap).
  2. Same round-trip on `ModelicaTestingLib.Examples.SimpleTest` (exercises `loadFile` dependency path).
  3. Timeout path: a model forced to fail translation, verify `error_message` surfaces.
  4. Variable filter: request 2 variables, verify `.mat` contains exactly {requested, time, diagnostics} (or close approximation).

### Golden fixtures

- `tests/fixtures/results_openmodelica/pid_controller_stdout.txt` — captured `omc` stdout from the PID smoke test.
- `tests/fixtures/results_openmodelica/pid_controller_res.mat` — small real result file, ~50KB.

Used by pure-parser / reader tests so they don't require a working `omc` install.

### Test count target

Baseline: 637 passing at HEAD.
After this work: **~655 passing** (+18 new; +0 regressions from the `mat_reader` hoist).

## Rollout order

Each step is its own commit so bisection stays clean.

1. **Hoist** `mat_reader` to `simulators/common/`, update Dymola imports, rename `read_dymola_mat` → `read_result_mat`. Full suite green (637).
2. **`mos_generator.py`** + unit tests.
3. **`log_parser.py`** + unit tests.
4. **`runner.py`** + `OpenModelicaConfig` + registry wiring.
5. **Integration test** against `Modelica.Blocks.Examples.PID_Controller` locally; commit fixtures.
6. **`testing.linux.json`** for `ModelicaTestingLib`; run `--accept --report`; visually verify HTML report; commit the fresh `OpenModelica/linux/` baselines.
7. **Docs update**: `CLAUDE.md` phase block describes the new backend; `docs/decisions.md` gains **D69 — OpenModelica batch backend** with scope + deferred items.

## Validation target (MVP-done criterion)

```bash
# One-time per machine
omc -e 'updatePackageIndex(); installPackage(Modelica); getErrorString();'

# From repo root
uv run modelica-testing \
    --config examples/modelica/ModelicaTestingLib/testing.linux.json \
    run --accept --report
```

All discovered `ModelicaTestingLib.Examples.*` tests pass, baselines written under `examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/OpenModelica/linux/`, HTML report opens cleanly, phase timings show up in the per-test `Timing` section.

Existing Windows-Dymola `testing.json` is **untouched** — lives in peer partition. TRANSFORM-UnitTests external consumer is unaffected.

## Out of scope (explicit)

- **Persistent-worker mode** via OMPython / `OMCSessionZMQ`. Follow-up after MVP.
- **`Capability.FMU_EXPORT`** via `buildModelFMU`. Future cross-backend enhancement; Dymola's equivalent is already experimental.
- **`check-openmodelica` CLI subcommand** (peer of `check-dymola`). Nice-to-have; not required for MVP.
- **CI matrix coverage**. Follows Phase 2.5 (gated on public repo).
- **Windows-side OpenModelica** testing. User's Windows is Dymola-owned.
- **Richer `simulator_setup` namespacing** (per-backend sections in `testing.json`). Current single-list-with-backend-specific-commands is sufficient; users own their own `testing.*.json`.
- **Auto-install of MSL**. One-time manual step documented in runner docstring.

## Risks & known-unknowns

1. **`ModelicaTestingLib` models may hit OM-vs-Dymola syntax divergence** (e.g., vendor-specific annotations, solver-only features). If so, scope grows: either patch the library to be portable, or declare a test `dymola-only` and skip on OpenModelica via recognizer metadata. Mitigation: run the sweep early (step 6) — if N tests need triage, bundle them into the same PR.
2. **Native executables from `omc` are not signed / not cleaned up**. `<work_dir>` grows per test; existing work-dir cleanup already handles this via `--clean` flag. No new concern.
3. **`variableFilter` regex may under-match** if tracked-variable names contain regex metacharacters — escape via `re.escape()` before joining with `|`. Covered in `test_openmodelica_mos.py`.
4. **`simulator_setup` commands that are Dymola-syntactic** (e.g., `Advanced.UI.TranslationInCommandLog := true;`) will fail on OpenModelica. User owns `testing.linux.json` content; document the constraint in the runner module docstring.

## Decision trail summary (for D69 when we write it)

- **Run path**: `omc` subprocess + `.mos` scripts (analogous to Dymola batch fallback). Persistent-worker (OMPython) deferred. Rationale: fastest to prove multi-backend abstraction; zero new pip deps; matches Dymola's historical progression.
- **Result reader**: hoisted `simulators.common.mat_reader` (shared code, not Dymola-owned). OM's `.mat` is format-compatible with Dymola's DSresult.
- **Phase timings**: parsed from `SimulationResult` record in stdout, bounded by sentinel markers. `{Model}_info.json` does not contain timings despite its name.
- **MSL bootstrap**: manual one-time `installPackage(Modelica)`. Auto-install rejected as MVP scope creep.
- **Validation target**: end-to-end `ModelicaTestingLib` run on Linux producing `OpenModelica/linux/` baselines. Hello-world-only target rejected as leaving the core abstraction claim unproven.
- **Cross-backend FMU export**: rejected for MVP. OM's `buildModelFMU` is plausible but compounds scope with already-experimental chain.
