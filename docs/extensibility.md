# Extensibility

> This is the contract every new Source, Discovery, Backend, Dataset, Metric, or Combinator must honor. It aligns with the six-layer abstraction in [architecture.md](architecture.md) and the vision in [vision.md](vision.md). Contracts here are specifications — not all are realized in code yet; a layer's "Current" box shows what exists today.

The framework's promise: **every layer is a plug-in point**. Adding a new simulator, a new metric, or a new way to discover tests must not require changes to the framework core. If a plug-in forces a core change, the abstraction is wrong and we fix the abstraction, not special-case the plug-in.

---

## Registry pattern

Every layer uses the same `@register("name")` decorator pattern already established for simulators:

```python
from dstf.backends import register, Backend

@register("Dymola")
class DymolaBackend(Backend):
    ...
```

Resolution: config specifies a name (`"backend": "Dymola"` or `"metric": "nrmse"`), a factory looks it up. Unknown names produce a clear error listing available plug-ins. Backends, metrics, combinators, dataset types, discovery strategies all use independent registries under `dstf.{backends,metrics,combinators,datasets,discovery}`.

Third-party plug-ins register via Python entry points (`[project.entry-points."dstf.backends"]`) so users can ship a backend in a separate package without patching ours.

---

## 1. Source

A Source describes *what* holds the behavior under test. Sources are not classes in the plug-in sense — they are tagged configuration that Discovery and Backends agree on. Each Source has a `type` field and a payload.

```json
{"source": {"type": "modelica-library", "path": "./MyLib"}}
{"source": {"type": "fmu-directory",    "path": "./fmus"}}
{"source": {"type": "julia-script",     "path": "./models/plant.jl"}}
{"source": {"type": "data-file",        "path": "./experiments/run_042.csv"}}
```

**Contract**: a Source type is registered alongside a default Discovery strategy and a set of compatible Backend types. The framework does not parse Sources itself — that's Discovery's job.

### Current

Two source types live today: `"modelica"` (default — `Config.source_path` points at a `package.mo` directory) and `"fmu"` (Phase 2 — `Config.source_path` is the FMU dir; per-test `"fmu"` field in `test_spec.json` names the binary). Discovery and backend selection gate on `Config.source_type`. Adding a new source means setting `source_type` and teaching `discover_tests` how to enumerate its tests; the backend layer below is independent.

---

## 2. Discovery

Discovery turns a Source into an iterable of `TestDefinition`s.

```python
class DiscoveryStrategy(Protocol):
    def discover(self, source: Source, config: Config) -> Iterator[TestDefinition]: ...
```

A `TestDefinition` is the layer's output: a model ID, tracked variables, simulation parameters (opaque to the framework — passed through to the Backend), and the MetricTree spec (or a default).

**Contract**: discovery is *pure* — it must not run simulations, import external packages, or depend on network resources. Multiple strategies may run against the same Source and their outputs are merged by model ID (current behavior for `.mo` scan + `test_spec.json`).

### Language-native discovery vs. neutral fallback

The "tests declared alongside the model itself" pattern is valuable in every ecosystem, but the *mechanism* is language-idiomatic. Each supported Source type may have its own native discovery strategy, and `test_spec.json` is always available as a universal fallback.

| Ecosystem | Native pattern | Universal fallback |
|---|---|---|
| Modelica | `UnitTests` component in `.mo` (implemented) | `test_spec.json` |
| Julia | macro or struct alongside model in `.jl` (planned) | `test_spec.json` |
| FMU | sidecar `<name>.test.json` next to `.fmu` (planned) | `test_spec.json` |
| Simulink | block annotation in `.slx` (planned) | `test_spec.json` |
| Data file | header metadata or sidecar (planned) | `test_spec.json` |

Native strategies are *optional* per ecosystem — a Source type can ship without one and rely on `test_spec.json`. Strategies compose: a Modelica Source can use both `.mo` scan + `test_spec.json` simultaneously (current behavior), with results merged by model ID.

### Current — pluggable recognizer registry (Phase 5 / PTA + follow-ups)

The Modelica `.mo` scan is itself a registry of **recognizers**. Each recognizer inspects one source file and emits a `RecognizerResult` describing a test it found. Discovery runs every registered recognizer that applies to the configured `source_type` and merges results by `model_id` (per-field, last writer wins).

- `discovery/recognizer.py` — `Recognizer` ABC, `RecognizerResult`, module-level registry (`get_recognizers(source_type)`), `applies_to_path(source_file, base)` for per-file pre-filtering.
- `discovery/mo_parser.py` — bundled `BundledModelicaUnitTestsRecognizer` (the `UnitTests` component + `experiment(...)` annotation pattern). Self-registers on import.
- `discovery/json_recognizer.py` — `JsonRecognizer`, configured by a JSON spec (no Python required). Modelica match types: `component-instantiation`, `extends`, `class-name-glob`, `all-of`, `any-of`. Field sources: `parameter`, `constant`, `experiment-annotation`, `annotation`. Supports `paths_include` / `paths_exclude` folder filter. Schema docs at the top of the module.
- `discovery/spec_parser.py` — `test_spec.json` reads (the universal fallback); merged after recognizers by `model_id`.

User-provided recognizers live on `Config.recognizers` (parsed from `testing.json`'s `"recognizers"` list); `Config.disabled_bundled` opts a bundled recognizer out by name. **Default is additive**: bundled returns None on files it doesn't recognize, so adding a custom recognizer doesn't remove anything. Disable is the explicit escape for the rare "ship-bundled-as-dep but don't discover its examples" case.

### Recognizer contract

A `Recognizer` declares two attributes and one method:

```python
class Recognizer(ABC):
    name: str                       # unique; used in diagnostics + disable_bundled
    applies_to: frozenset[str]      # Config.source_type values it applies to

    @abstractmethod
    def recognize(self, source_file: Path) -> Optional[RecognizerResult]: ...
```

The match-type vocabulary is **per-source-type**. Modelica has `component-instantiation`, `extends`, …; FMU would declare its own (e.g. `model-description-vendor-extension`); each source type owns its parser and its match types.

### Deferred (capture-and-revisit)

- `not-of` match composition (single-child negation) — useful but validation-by-leaf-types needs special-casing for negation. No concrete user need yet.
- Cross-source recognizers (FMU vendor extensions, Julia macros). Same registry shape; each source type owns its match-type vocabulary.
- `quick_check(content) -> bool` optimization on recognizers if per-file regex becomes a bottleneck on very large libraries.

---

## 3. Backend

Backends execute tests and produce Datasets. See [architecture.md](architecture.md) for the target ABC.

### Capabilities (mandatory to declare)

Each Backend declares a frozen set of capabilities:

| Capability | Meaning |
|---|---|
| `supports_persistent_workers` | Holds loaded model(s) in memory across tests (performance: no per-test startup). |
| `supports_batch_fallback` | Exposes a non-interactive script-driven mode when persistent workers aren't available. |
| `supports_fmu_export` | Can export a test artefact as an FMU (enables cross-backend verification). |
| `supports_experiment_ingest` | Reads pre-recorded data instead of simulating. |

Features in the framework (CLI flags, report sections, cross-backend chains) gate on capabilities, not on backend names. A new backend that declares `supports_fmu_export` automatically participates in cross-backend verification with no framework change.

### Produced datasets (mandatory to declare)

```python
produced_datasets: frozenset[DatasetType]  # e.g. {DatasetType.TimeSeries, DatasetType.Events}
```

Metrics check at config-resolution time that the Backend produces the Dataset types they need, and error early with a clear message if not.

### Contract summary

A Backend must:
1. Declare `capabilities` and `produced_datasets` as class attributes.
2. Implement `run_tests(tests) -> list[BatchManifest]` (or inherit a default that uses `run_single_test`).
3. Implement `read_result(test) -> Dataset` returning a typed Dataset.
4. Implement `export_fmu(test) -> Path` iff `supports_fmu_export`.
5. Use `ProgressReporter` (backend-agnostic, already exists) for live-dashboard status.
6. Put all tool-specific code under `backends/<name>/`. No tool-specific imports in framework core.

### Persistent-worker contract

If your backend can hold a loaded model in memory across tests (declares `Capability.PERSISTENT_WORKERS`), inherit from `PersistentRunnerBase` (in `simulators/base.py`) and supply a `Worker` subclass — you do **not** re-implement the dispatch loop. The template owns the orchestration; you fill in backend-specific construction and lifecycle hooks.

**`Worker` ABC** (in `simulators/base.py`) declares the long-lived-process contract every persistent backend's worker must satisfy:

| Method | Required | Contract |
|---|---|---|
| `__init__(worker_id, config, ...)` | yes | Subclass extends with backend-specific args (e.g. `DymolaConfig`, an interface class). MUST call `super().__init__(worker_id, config)`. |
| `start()` | yes | Bring the process up, apply startup (library loads, framework settings). Raise on failure. |
| `close(grace=...)` | yes | Tear down. Try graceful first; hard-kill if the graceful path doesn't return within `grace` seconds. Idempotent — safe on workers that never started or already closed. |
| `is_alive()` | yes | True iff the worker can accept another test. |
| `run_test_with_timeout(test, test_key, timeout, progress=None)` | yes | Run one test with a watchdog. **MUST NOT** raise on test-level failures — return a `TestRunResult(success=False)` instead. **MAY** raise on worker-level catastrophes (subprocess died, pipe broken) so the dispatch loop's restart logic intervenes. |
| `export_fmu(test, output_dir)` | iff `FMU_EXPORT` | Default raises `NotImplementedError`. |

**`PersistentRunnerBase`** (subclass of `SimulatorRunner`) provides the full `run_tests` template:

```
setup_before_workers → ProgressReporter → assign_test_keys
    → BatchManifest → make_worker × N → start in parallel
    → filter live workers (raise if zero) → dispatch with restart
    → close all workers → finalize progress
```

You fill in:

| Hook | Default | Override when |
|---|---|---|
| `worker_cls` (class attr) | `None` | Always — set to your `Worker` subclass. |
| `backend_label` (class attr) | `""` | Always — used in headers and thread names ("via persistent **<label>** workers"). |
| `make_worker(worker_id)` | `worker_cls(worker_id, self.config)` | Your worker constructor needs extra args beyond `(worker_id, config)`. Read state stashed by `setup_before_workers`. |
| `setup_before_workers()` | no-op | Backend has runtime patches that must apply before any worker spawns (Dymola: log filter + parallel-startup lock). |
| `preflight(config)` (classmethod) | no-op | Backend has an external dependency (Python module, native binary, license file). Raise `RuntimeError` with an install hint when missing — the CLI surfaces it verbatim. |
| `starting_workers_message(n)` | `"Starting N <label> worker(s)..."` | You want a backend-specific notice (Julia: warmup-time warning). |
| `max_restarts_per_worker` (class attr) | `3` | Restart budget needs tuning. |
| `persistent_runner_cls()` (classmethod) | `cls` | Don't override — the default returns self so the CLI doesn't recurse. |

Plus on the **batch** runner class (the one decorated with `@register("YourBackend")`):

| Hook | Default | Override when |
|---|---|---|
| `persistent_runner_cls()` (classmethod) | `None` (batch-only) | Backend ships a persistent variant. Lazy-import + return its class so the optional dep isn't pulled in during a plain batch run. |

**Inheritance order matters.** Persistent runners inherit from both `PersistentRunnerBase` *and* the batch runner. The MRO must put `PersistentRunnerBase` first so its `run_tests` template wins over the batch runner's per-test override:

```python
class PersistentDymolaRunner(PersistentRunnerBase, DymolaRunner):
    worker_cls = DymolaWorker
    backend_label = "Dymola"
    ...
```

Worked example: `simulators/openmodelica/persistent_runner.py` is the smallest of the three live persistent runners — read it as the canonical reference.

### Current

`SimulatorRunner` ABC with five concrete implementations: `DymolaRunner`, `OpenModelicaRunner` (native Modelica), `FmpyRunner` (FMU), `JuliaRunner` (ModelingToolkit), and `PythonRunner` (arbitrary `simulate()`). The `Capability` + `DatasetType` enums and the `capabilities` / `produced_datasets` class-attribute contract gate feature availability per backend.

- `DymolaRunner` declares `{PERSISTENT_WORKERS, BATCH_FALLBACK, FMU_EXPORT}` — the last is currently a placeholder until a cross-backend verification feature wires it.
- `FmpyRunner` declares `{PERSISTENT_WORKERS}` — no batch fallback (FMPy *is* the Python path), no FMU export (FMPy consumes FMUs), no experiment ingest.

Both produce `{TIME_SERIES}`. The framework doesn't yet *gate* features on the declarations — consumers of `capabilities` (feature toggling, CLI warnings) are a later phase once a concrete decision depends on capability flags. Rename `SimulatorRunner` → `Backend` is still deferred; the existing name now applies to both a Modelica-backed *and* FMU-backed implementation, which makes the rename more valuable but also a cleaner one-shot change to land at a single time.

---

## 4. Dataset

A Dataset is the typed output of a Backend run, and the input to a Metric. Datasets are not extensible in the plug-in sense — the framework defines a closed set so Metrics have something concrete to consume. Adding a Dataset type is a framework change, not a user change.

Types:
- `TimeSeries` — `(time: np.ndarray, variables: dict[str, np.ndarray])` plus diagnostics + statistics. The current implicit dataset.
- `Scalars` — named scalar outputs (final-value tests, calibration scores, energy balances).
- `Events` — ordered list of `(time, label, data)` tuples (Gap D).
- `Spectrum` — frequency-domain representation (FFT / Welch / Lomb-Scargle). For spectral-coherence metrics.
- `Distribution` — samples or fitted distribution (Monte Carlo, SDE ensembles, KS metrics).
- `Field` — *reserved*. Spatial / PDE data. Not implemented.

**Contract**: each Dataset type has a well-defined numpy-friendly shape. Metrics declare which Dataset types they accept. Backends declare which they produce. Incompatible combinations fail at config resolution with a clear error, not at runtime.

### Current

Only `TimeSeries` materialized (as `TestResult.variables` — list of `VariableResult`). The `DatasetType` enum exists and backends declare `produced_datasets`, but no typed `Dataset` wrapper class has been introduced — it would be gratuitous churn with one concrete type. The wrapper lands when a second Dataset type (e.g. `Events` for Gap D metrics) actually needs to discriminate.

---

## 5. Metric

A Metric scores a Dataset against a named baseline (or a pair of baselines, for cross-baseline comparison) and returns a structured result.

```python
class Metric(Protocol):
    accepts: frozenset[DatasetType]

    def evaluate(
        self,
        actual: Dataset,
        reference: Dataset,             # resolved from a named baseline at eval time
        config: MetricConfig,
    ) -> MetricResult: ...

@dataclass(frozen=True)
class MetricResult:
    passed: bool
    score: float                     # normalized [0, 1] if meaningful; else raw
    diagnostics: dict[str, Any]      # per-variable NRMSE, tube violations, event mismatches, ...
```

The MetricTree node referencing a metric specifies `"against": "<baseline-name>"` so a single test can carry multiple named baselines (see [vision.md](vision.md) "Multiple named baselines"). Cross-baseline comparison metrics (e.g. "how similar are Dymola and FMU outputs?") take two baseline names: `"against": ["dymola", "fmu"]`.

**Contract**:
1. `accepts` declares which Dataset types the metric consumes (pre-check at config time).
2. `evaluate` is *pure* — no I/O, no mutation of inputs, no global state. Deterministic given inputs.
3. `diagnostics` must be JSON-serializable (travels to reports + reference files).
4. Metric-specific configuration is typed (frozen dataclass), registered with the metric, and validated on construction.

### Currently registered

- **`nrmse`** — piecewise NRMSE with event-boundary handling. `NrmseMode`.
- **`tube`** — envelope comparison, three width modes. `TubeMode`.
- **`points`** — declared-checkpoint comparison; empty list ⇒ final-value-only, non-empty ⇒ multi-point with abs/rel y-tolerance + x-tolerance box. `PointsMode`. D84.
- **`range`** — signal-only bounds check (no baseline needed). `RangeMode`. D53.
- **`event-timing`** — compare event instants via duplicate-time detection. `EventTimingMode`. D62.
- **`dominant-frequency`** — FFT peak comparison. `DominantFrequencyMode`. D62.
- **`x-tolerance`** *(planned)* — pyfunnel x+y funnel comparison.
- **`fréchet`** / **`iso-18571`** *(future)* — shape-sensitive metrics.
- **`ks-distribution`** *(future)* — stochastic regression.

### User metrics

A user registers a metric in their own package:

```python
@register("power-balance")
class PowerBalanceMetric(Metric):
    accepts = frozenset({DatasetType.TimeSeries})

    def evaluate(self, actual, reference, config):
        ...  # domain-specific pass/fail + score
```

And references it in `test_spec.json`:

```json
{"metric": "power-balance", "config": {"tolerance_watts": 5.0}}
```

The framework requires no change.

---

## 6. MetricTree / Combinators

MetricTree composes Metrics with Boolean / weighted logic. Leaves are `Metric` evaluations; internal nodes are `Combinator`s.

### Built-in combinators

- **`and`** — all children pass; score = min. The current implicit behavior.
- **`or`** — any child passes; score = max.
- **`weighted`** — weighted sum of child scores against a threshold. Direction-aware (`less` for NRMSE-like, `greater` for tube-like). Parameters: `{weights: [float], threshold: float, direction: "less"|"greater"}`. D61.
- **`k-of-n`** — at least K children pass.
- **`warn`** — single-child wrapper that always passes the parent but surfaces the child's failing diagnostics as warnings in the report.

### Combinator contract

```python
class Combinator(Protocol):
    def combine(self, children: list[MetricResult]) -> MetricResult: ...
```

Pure function. Diagnostics must preserve enough of the children's diagnostics that a failing branch can be rendered in the report. Combinators are registered via `dstf.combinators`.

### Schema in test_spec.json

Simple case (unchanged, still valid — interpreted as flat AND of `nrmse` per variable):

```json
{"tolerance": 0.01, "variables": ["pipe.T[1]", "pipe.m_flow"]}
```

Composed case (landed in Phase 3 — schema lives under the `metrics` key):

```json
{
  "metrics": {
    "combinator": "or",
    "children": [
      {"metric": "tube", "variable": "pipe.T[1]", "tube_rel": 0.02},
      {"metric": "nrmse", "variable": "pipe.T[1]", "tolerance": 0.05}
    ]
  }
}
```

Leaf metrics available today: `nrmse`, `tube`, `points`, `range`. Leaf params (tolerance, tube_*, min/max) live flat on the leaf node — same field names as the legacy `variable_overrides`. Combinators: `and`, `or`, `k-of-n` (requires `k`), `warn` (exactly one child).

When `metrics` is set, the tree fully controls scoring on that test — legacy `comparison.variable_overrides` is ignored (the same fields move into each leaf's params).

### Current

Phase 3 wired MetricTree end-to-end:
- `comparison/metric_tree.py` — combinators + `MetricResult` (landed Phase 1).
- `comparison/tree_spec.py` — parses user specs (`"metrics"` block) into `LeafSpec` / `CombinatorSpec` with path-bearing validation errors.
- `comparison/tree_eval.py` — walks a parsed spec against simulation + reference data to produce an evaluated `MetricResult` tree, and serializes the result for report rendering.
- `compare_test()` derives `TestComparison.passed` from the tree root. Users authoring a `metrics` block get their tree; others get the implicit flat-AND (behavior-preserving for all pre-Phase-3 specs).
- Per-test HTML report renders the tree when user-authored (`comparison.html`).

Leaf contract is validated across four metrics spanning two shapes: three reference-consuming (`nrmse`, `tube`, `points`) and one signal-only (`range` — bounds come from the spec, not a baseline).

Deferred (Phase 4+): multi-baseline leaves (`"against": "experiment"`), cross-backend verification, additional leaf types (event-timing, spectral, Fréchet, KS), `weighted` combinator.

---

## Cross-cutting guarantees

Every plug-in, regardless of layer, must honor:

1. **Determinism** — same inputs produce the same outputs. No wall-clock, no PID, no randomness without an explicit seed.
2. **JSON-serializable configuration** — all plug-in config must round-trip through `test_spec.json` and the reference JSON.
3. **Clear error messages on misuse** — "Backend `Foo` does not produce `Events`; metric `event-timing` requires it" is a framework-level check, not a runtime crash.
4. **No framework-core edits** — if adding a plug-in requires editing `cli.py`, `config.py`, or anything outside `backends/`, `metrics/`, `combinators/`, `discovery/`, the abstraction is wrong. File an issue before working around it.
5. **Test fixture included** — every plug-in ships with at least one test against a known-good input.

---

## Where to put new code

```
src/dstf/
  backends/
    <name>/           # all tool-specific code for backend <name>
  metrics/
    <name>.py         # single-file metrics; <name>/ for larger
  combinators/
    <name>.py
  discovery/
    <name>.py         # one file per strategy
  datasets/
    __init__.py       # closed set; framework-internal

examples/
  modelica/
    ModelicaTestingLib/    # Modelica demo library (also used as pytest fixture)
  fmu/
    reference-fmus/        # FMI org's Reference-FMUs (pinned)
  julia/                   # planned
  data-file/               # planned (CSV experiment fixtures)
```

Each directory under `examples/` demonstrates one Source type end-to-end — a working reference a user can copy. Pytest fixtures (captured artefacts for unit-testing internals like the MAT reader) remain under `tests/fixtures/` and are separate from `examples/`.

Third-party plug-ins live in separate packages and register via entry points. The same layout applies there.
