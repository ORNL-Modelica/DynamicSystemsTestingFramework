# Extensibility

> This is the contract every new Source, Discovery, Backend, Dataset, Metric, or Combinator must honor. It aligns with the six-layer abstraction in [architecture.md](architecture.md) and the vision in [vision.md](vision.md). Contracts here are specifications — not all are realized in code yet; a layer's "Current" box shows what exists today.

The framework's promise: **every layer is a plug-in point**. Adding a new simulator, a new metric, or a new way to discover tests must not require changes to the framework core. If a plug-in forces a core change, the abstraction is wrong and we fix the abstraction, not special-case the plug-in.

---

## Registry pattern

Every layer uses the same `@register("name")` decorator pattern already established for simulators:

```python
from modelica_testing.backends import register, Backend

@register("Dymola")
class DymolaBackend(Backend):
    ...
```

Resolution: config specifies a name (`"backend": "Dymola"` or `"metric": "nrmse"`), a factory looks it up. Unknown names produce a clear error listing available plug-ins. Backends, metrics, combinators, dataset types, discovery strategies all use independent registries under `modelica_testing.{backends,metrics,combinators,datasets,discovery}`.

Third-party plug-ins register via Python entry points (`[project.entry-points."modelica_testing.backends"]`) so users can ship a backend in a separate package without patching ours.

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

Implicit. `config.package_path` is hardcoded to "Modelica library with `package.mo`". The forward work is to introduce a `Source` tag and generalize `package_path` into `source.path`.

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

### Current

Two strategies, both Modelica-flavored:
- `mo_parser.py` — scans `.mo` for `UnitTests` components.
- `spec_parser.py` — reads `test_spec.json`.

Merged by `test_registry.discover_tests()`. The `test_spec.json` format is already ecosystem-neutral; the `.mo` scan is Modelica-specific (appropriate — it's one strategy among many).

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

### Current

`SimulatorRunner` ABC with two concrete implementations: `DymolaRunner` (native Modelica) and `FmpyRunner` (FMU). Phase 1.2 introduced the `Capability` + `DatasetType` enums and the `capabilities` / `produced_datasets` class-attribute contract; Phase 2.3 validated the contract with a second backend.

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

### Currently registered (forward)

- **`nrmse`** — piecewise NRMSE with event-boundary handling. Current `NrmseMode`, refactored.
- **`tube`** — envelope comparison, three width modes. Current `TubeMode`, refactored.
- **`final-only`** — compare only final values. Current `FinalOnlyMode`, refactored.
- **`event-timing`** *(planned)* — assert events occur within a time tolerance.
- **`x-tolerance`** *(planned)* — pyfunnel x+y funnel comparison.
- **`spectral-coherence`** *(planned)* — frequency-domain similarity.
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

- **`and`** — all children pass; score = min (or product). The current implicit behavior.
- **`or`** — any child passes; score = max.
- **`weighted`** — weighted sum of child scores against a threshold. Parameters: `{child: weight, threshold: float}`.
- **`k-of-n`** — at least K children pass.
- **`warn`** — single-child wrapper that always passes the parent but surfaces the child's failing diagnostics as warnings in the report. Used to include comparisons (e.g. against experimental data) as informational without gating pass/fail.

### Combinator contract

```python
class Combinator(Protocol):
    def combine(self, children: list[MetricResult]) -> MetricResult: ...
```

Pure function. Diagnostics must preserve enough of the children's diagnostics that a failing branch can be rendered in the report. Combinators are registered via `modelica_testing.combinators`.

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

Leaf metrics available today: `nrmse`, `tube`, `final-only`, `range`. Leaf params (tolerance, tube_*, min/max) live flat on the leaf node — same field names as the legacy `variable_overrides`. Combinators: `and`, `or`, `k-of-n` (requires `k`), `warn` (exactly one child).

When `metrics` is set, the tree fully controls scoring on that test — legacy `comparison.variable_overrides` is ignored (the same fields move into each leaf's params).

### Current

Phase 3 wired MetricTree end-to-end:
- `comparison/metric_tree.py` — combinators + `MetricResult` (landed Phase 1).
- `comparison/tree_spec.py` — parses user specs (`"metrics"` block) into `LeafSpec` / `CombinatorSpec` with path-bearing validation errors.
- `comparison/tree_eval.py` — walks a parsed spec against simulation + reference data to produce an evaluated `MetricResult` tree, and serializes the result for report rendering.
- `compare_test()` derives `TestComparison.passed` from the tree root. Users authoring a `metrics` block get their tree; others get the implicit flat-AND (behavior-preserving for all pre-Phase-3 specs).
- Per-test HTML report renders the tree when user-authored (`comparison.html`).

Leaf contract is validated across four metrics spanning two shapes: three reference-consuming (`nrmse`, `tube`, `final-only`) and one signal-only (`range` — bounds come from the spec, not a baseline).

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
src/modelica_testing/
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
