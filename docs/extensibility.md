# Extensibility

> This is the contract every new Source, Discovery, Backend, Dataset, Metric, or Combinator must honor. It aligns with the six-layer abstraction in [architecture.md](architecture.md) and the vision in [vision.md](vision.md). Contracts here are specifications — not all are realized in code yet; a layer's "Current" box shows what exists today.

The framework's promise: **every layer is a plug-in point**. Adding a new backend, a new metric, or a new way to discover tests must not require changes to the framework core. If a plug-in forces a core change, the abstraction is wrong and we fix the abstraction, not special-case the plug-in.

---

## Registry pattern

Every layer uses the same `@register("name")` decorator pattern already established for backends:

```python
from dstf.backends import register, Backend

@register("Dymola")
class DymolaBackend(Backend):
    ...
```

Resolution: config specifies a name (`"backend": "Dymola"` or `"metric": "nrmse"`), a factory looks it up. Unknown names produce a clear error listing available plug-ins. Backends, metrics, combinators, dataset types, discovery strategies all use independent registries under `dstf.{backends,metrics,combinators,datasets,discovery}`.

### Shipping a plug-in in your own package

Registration happens as an import side effect, so the framework has to import your module. It finds it through **one** entry-point group, `dstf.plugins` — declared in your package's `pyproject.toml`:

```toml
[project.entry-points."dstf.plugins"]
acme-backend = "acme_dstf.backend"           # a module: importing it registers
acme-metrics = "acme_dstf.metrics:register"  # a callable: invoked with no args
```

An entry point resolving to a **module** is merely imported (its `@register` / `register_metric` calls run at import time); one resolving to a **callable** is called with no arguments. Anything else is an error.

One group, not one per layer: a plug-in package routinely ships a backend *and* the metrics that score it, and splitting the declaration by layer only makes the author guess which group a given registration belongs in.

`dstf.cli.main` calls `load_plugins()` before dispatching any subcommand, so every command sees the same registries. Library users call `dstf.load_plugins()` themselves. Loading is idempotent. **A plug-in that fails to import or call is a hard error** (`ConfigError`, exit 2, naming the entry point) — a metric that quietly failed to register does not fail quietly downstream, it changes scoring semantics.

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

Four source types live today: `"modelica"` (default — `Config.source_path` points at a `package.mo` directory), `"fmu"`, `"julia"` and `"python"` (`Config.source_path` is the FMU / project / script dir; the per-test `"source"` field in `test_spec.json` names the binary or file). Only `"modelica"` is branched on in framework code — the package.mo lookup in `config.py` and the `.mo` recognizer scan in `discover_tests`; the other values simply skip both and select recognizers via `Recognizer.applies_to`. Adding a new source means setting `source_type` and teaching `discover_tests` how to enumerate its tests; the backend layer below is independent.

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

Each Backend declares a frozen set of capabilities as a class attribute:

```python
capabilities: frozenset[Capability]  # members of backends.types.Capability
```

| `Capability` member | Meaning |
|---|---|
| `PERSISTENT_WORKERS` | Holds loaded model(s) in memory across tests (performance: no per-test startup). |
| `BATCH_FALLBACK` | Exposes a non-interactive script-driven mode when persistent workers aren't available. |
| `FMU_EXPORT` | Can export a test artefact as an FMU (enables cross-backend verification). |
| `LIVE_RESULTS` | The result file grows while the run is in progress and can be read then; `live_result_path` says which file. See "Live results" below. |
| `CANCEL` | Can stop a test that is running, from another thread (the simulation pool's cancel). |

Features in the framework (CLI flags, report sections, cross-backend chains) gate on capabilities, not on backend names. A new backend that declares `FMU_EXPORT` automatically participates in cross-backend verification with no framework change.

Declare a capability on the class that actually has it. Where a backend ships a persistent variant, the two classes can differ — Dymola's FMU export needs a live `DymolaInterface`, so `PersistentDymolaBackend` declares `FMU_EXPORT` and the batch `DymolaBackend` does not; `CANCEL` sits on the three persistent variants (the stop is their worker's) and on `PythonBackend` (its `cancel_test`). Registration-time validation (`backends._validate_capabilities`) enforces flag ⟹ method override, but only for `@register`-ed classes; persistent variants are reached through `persistent_backend_cls`, so their declarations are covered by tests.

### Produced datasets (mandatory to declare)

```python
produced_datasets: frozenset[DatasetType]  # e.g. {DatasetType.TIME_SERIES, DatasetType.EVENTS}
```

Metrics check at config-resolution time that the Backend produces the Dataset types they need, and error early with a clear message if not.

### Contract summary

A Backend must:
1. Declare `capabilities` and `produced_datasets` as class attributes.
2. Implement `run_tests(tests) -> list[BatchManifest]` (or inherit a default that uses `run_single_test`).
3. Implement `read_result(test) -> Dataset` returning a typed Dataset.
4. Implement `export_fmu(test) -> Path` iff the class declares `Capability.FMU_EXPORT`.
   Override `live_result_path(test_dir) -> Path` iff it declares `Capability.LIVE_RESULTS`.
   Implement `cancel_test(test_key) -> bool` iff a class WITHOUT persistent workers declares `Capability.CANCEL` (see "The simulation pool").
5. Use `ProgressReporter` (backend-agnostic, already exists) for live-dashboard status.
6. Put all tool-specific code under `backends/<name>/`. No tool-specific imports in framework core.

Optionally, two **classmethods** let both tiers plot a variable the test does
not track (the `plots` key, `docs/usage.md`):

- `result_names(test_dir) -> list[str] | None` — every variable name in the
  result file under `test_dir` (`work_dir/<test_key>`), without `time`;
  `None` when the backend keeps no file it can list.
- `result_series(test_dir, names) -> dict[str, tuple[time, values]]` — the
  named series the file has, as 1-D float arrays on one time base; a name it
  lacks is left out.

They are classmethods because the report reads a test's files without
instantiating a backend. The base class answers `None` / `{}`, so a backend
without them stays valid and its tests plot tracked variables only. Neither
may raise on a missing, half-written or malformed file: one test's file must
not break a whole report. `backends/common/result_files.py` holds the three
bundled formats (`.mat`, `result.json`, `.npz`) as name/series pairs.

### The `.mat` reader: rows are counted from the file's size

`backends/common/mat_reader.py` is the one reader of DSresult `.mat` files
(Dymola's `dsres.mat`, OpenModelica's `result_res.mat`). Its rule:

- **How many time points a file holds comes from its size, never from the
  count in its header.** The trajectory matrix `data_2` is the file's last
  matrix and both tools write its header before its rows. Dymola's count
  starts at 1 and stays behind the rows while a run is in progress;
  OpenModelica's is 0 until the run ends (unless `-mat_sync=1`, below).
  Neither is completed when a run is killed. `read_result_mat` and
  `list_result_mat_variables` therefore read the whole time points present:
  a file cut mid-point yields the points before the cut, and a short file
  never raises.
- **Readable is not finished.** A verdict still asks for a finished file,
  and what says so is the header's own count: it equals the points present,
  there is at least one, and no byte follows them. `read_mat_time_extents`
  and `mat_reaches_time` (the Dymola completion check, the OpenModelica disk
  rescue) answer for a finished file only. `mat_shortfall(path)` says in
  words why a file is not one; both DSresult backends' `read_result` ask it
  (`result_variables.unfinished_result`), so the rows of a killed run come
  back to be **shown**, inside a result that is a failure — nothing scores
  them or offers them as a baseline.

A backend that writes this format reuses the reader and the gate; one that
writes another format owes the same two properties to its own reader if it
wants its partial results shown.

### Live results: reading a result while its run writes it

A backend whose result file grows during a run declares
`Capability.LIVE_RESULTS` and overrides one classmethod:

```python
@classmethod
def live_result_path(cls, test_dir: Path) -> Path:   # test_dir = work_dir/<test_key>
    return test_dir / "dsres.mat"
```

The base class returns `None`. Registration fails for a class that declares
the flag without the override. Dymola and OpenModelica declare it (batch and
persistent classes); Python, Julia and FMPy write their result once, at the
end, and do not.

The file is followed with `backends/common/live_mat.py`:

```python
reader = LiveMatReader(path, max_rows=2000)   # one per result-file path; no I/O yet
snap = reader.poll(names)                     # about once a second
```

`poll(names)` returns an immutable `LiveSnapshot`:

| Field | Meaning |
|---|---|
| `state` | `"gone"` — no file at the path (not created yet, or removed); `"starting"` — a file whose header matrices are not whole yet; `"running"` — rows are served as they arrive. A finished file also reads `"running"`: the reader does not judge whether a run has ended. |
| `run` | Which file under the path this is, from 0. It goes up each time the reader starts over; a caller that accumulates series drops them when it changes. |
| `rows` | Whole rows (time points) the file holds. |
| `time` | The time of the last whole row; `None` before the first. |
| `series` | `{name: LiveSeries(time, values)}` — for each requested name the file has, the samples that are **new since that name was last served** (empty arrays when none are; a name the file lacks is left out). Read-only arrays in the file's own precision, resolved by the full reader's code: an alias keeps its sign, a parameter is a constant. |

What the reader guarantees, and why:

- **Nothing is held between polls**: open, `fstat`, read, close. A
  persistent worker wipes a test's directory before each run, and on Windows
  a held handle makes that removal fail.
- **No memory map**: a map over a file that is then truncated in place kills
  the process.
- **Only new rows are read**: in the steady state one read, from the last
  row served through the newest. A read of rows is bounded at 16 MB.
- **It never raises** for what the file is: short, half-written, vanishing,
  being recreated, or not a result at all.
- **A new run is recognised** when the file was gone at a poll, its
  (device, inode) changed, it got smaller, or the last row served no longer
  reads back as it did (a recreated file takes the removed one's inode on
  ext4, so identity and size alone can say "the same file, longer").
- **A poll returns at most `max_rows` samples per name.** A longer backlog —
  the first poll of a run long under way, a name first asked for late — is
  thinned to that many, evenly spaced and always ending on the newest row.
  Thinned rows are not served later; the finished file is read by
  `read_result_mat`.
- A name's samples, concatenated over the polls of one run, are that name's
  series, whenever it was first asked for.

A reader belongs to one poller; it is not thread-safe.

**`Config.live_results`** (default off) is the one run option: a caller that
will watch a run sets it before building the backend. There is no
`testing.json` key and no CLI flag, so `dstf run` is never changed by it. A
backend whose file would otherwise reach the disk late pays to write it
promptly: OpenModelica adds `simflags="-mat_sync=1"` to its `simulate()`
call (without it rows arrive a 4 KB block at a time). Dymola appends as it
goes and needs nothing. Under `-mat_sync=1` OpenModelica also keeps the
header's count current, so a watched run's file is "finished" by its header
at every moment; what stops a killed one from passing is that it stopped
short of the stop time, and the run's recorded verdict.

### The `VariableResult` contract: name vs expression

Each `VariableResult` a backend produces carries two texts (D132):

- **`name`** — the name the RESULT FILE gives the variable. This is the
  identity: overrides, metric-tree leaves, `plots` series, baseline entries,
  figure `meta.id`s, `data-variable` attributes and test markers are all
  keyed by it. A backend never invents one and never renames a variable to
  something more readable; a nameless `VariableResult` is a bug, and no
  consumer falls back to the index.
- **`expression`** (optional, `""` by default) — the text the model wrote for
  that variable, when the source has such a text and the result file's name
  does not carry it. Only the Modelica `UnitTests` path has one today: the
  result file names those variables positionally (`unitTests.x[3]`), and the
  expression is what `x={...}` said. FMPy, Julia and Python results carry
  result-file names already, so their `expression` stays `""`.

`expression` is a LABEL and is never used as a key.
`backends/types.py::display_label(name, expression)` is the one rule for what a person reads
(`expression or name`), and `variable_texts` is the scope rule above it: over
every name a TEST's page can show — tracked variables, scored comparisons and
plot series — it adds the name beside a label another of them also carries
(`a.Q (unitTests.x[1])`). A title that already shows the muted name reads the
plain label; every other surface reads the disambiguated text.
Producing it is `discovery/unit_tests.py::unit_test_variables` — the one
place a `UnitTests` variable is named — not each backend's own string
formatting.

**The one exception** is overlay matching, in
`reporting/overlay_loader.py::attach_overlays_to_trajectories`. An overlay is
another source's data — a companion is a different library's or a different
tool's run — and it names the quantity the way ITS result file does, so a
positional `unitTests.x[3]` is a name no other source can be expected to use.
Which of a companion's variables draws on which of the test's is therefore
recorded in the companion's own metadata, as the `variables` mapping
`dstf companion add --map` writes; where a companion has one, it is the whole
rule, and a trajectory it does not name draws nothing from that companion.
Only a COMPANION with no mapping falls back on spellings — the ones registered
before there was a mapping to write. It tries, in order, the companion variable
whose name is the trajectory's name, whose name is its expression, and whose own
expression is either. A fallback step that finds two candidates draws nothing and
warns: `x={a.Q, a.Q}` is legal, so one expression can belong to two of a
companion's variables, and guessing puts somebody else's curve under the wrong
plot. Nothing else falls back: a soft_check, a cross-backend import stored as
one, and an auto-discovered sibling-backend reference are DSTF's own data keyed
by the names this test uses, so they match BY NAME ONLY — and a soft_check is
also SCORED by name (`against:`), so the curve drawn for it has to be the curve
scored for it. A companion whose recorded mapping could not be read (a
hand-edited `variables`) keeps its record's authority too: it says what was
wrong on the overlay table and draws only what survived reading it.

### Persistent-worker contract

If your backend can hold a loaded model in memory across tests (declares `Capability.PERSISTENT_WORKERS`), inherit from `PersistentBackendBase` (in `backends/persistent.py`) and supply a `Worker` subclass — you do **not** re-implement the dispatch loop. The template owns the orchestration; you fill in backend-specific construction and lifecycle hooks.

**`Worker` ABC** (in `backends/persistent.py`) declares the long-lived-process contract every persistent backend's worker must satisfy. Two of its methods — `run_test_with_timeout` and `close` — are **template methods** implemented once in the base: the watchdog thread, the timeout kill, the process-tree kill, the disk rescue and the restart-accounting flags were identical in every backend. You supply hooks, not a watchdog.

What the base guarantees, for every worker: `run_test` executes on a daemon exec thread joined with the test's timeout; on expiry the worker is `close()`d (graceful attempt on its own daemon thread, abandoned after `timeout_kill_grace`, then `kill_process_tree` on every tracked PID and on the children recorded by the tree walks `close()` takes before the graceful attempt and every 0.25 s during it — a child that first appears *inside* that window, as dymosim does when the timeout lands during translation, is only reachable while its parent is still alive); `_evaluate_from_disk` then gets a chance to rescue a simulation that finished just before the kill. Timeout results carry `worker_killed=True` (a *healthy* worker died because the test was slow — the dispatch loop must not charge it against `max_restarts_per_worker`); worker exceptions leave it `False` (a genuinely broken worker DOES cost budget). `self.pids` and `self._n_restarts` are declared by `Worker.__init__`, so `super().__init__(worker_id, config)` is not optional.

Three more things the base does for every worker, each with a hook for what only the backend knows:

- **A bounded start.** Nothing calls `start()` bare: `start_within()` runs it on a daemon thread against `startup_bound()` — `worker_startup_timeout` from `testing.json`, else the class's `startup_timeout` (600 s on every backend: the bound is for a tool that hangs, and a large library is minutes of `loadFile` in a live omc) — and raises `WorkerStartError` at once when the tracked process is gone while `start()` still waits on it. A start that ran out says so with the step and the key: `did not start within 600 s — omc is still running during loadFile("...") (raise "worker_startup_timeout" in testing.json if this library needs longer)`. While a start is in flight `Worker.starting()` returns `(self._step, seconds so far)`; the simulation pool lists those as `SimulationPool.starting`. OMPython waits for omc's reply with no bound, so an omc that died during `loadModel` used to hold a run for good, every test "queued". The reason is in the backend's words: what `start()` raised, or `process_detail()` (exit code or "still running", the step in `self._step`, the log's tail). A start given up on is closed again if it ever returns, and that worker object is never started again. When no worker starts, `run_tests` fails every test with `No <label> worker could start — worker 0: <reason>; ...` and raises the same sentence.
- **A death that takes its children with it.** While a test runs the watchdog looks at the tracked process every `_CHILD_WATCH_SECONDS` (0.5 s) and keeps its live children on record. A tool that dies by itself fails the test (`Worker process exited during the test`), and `close()` kills the recorded children — an orphaned dymosim is out of reach of a tree walk from a parent that no longer exists. A child born after the last look is on no record; on Linux it is found all the same, by a mark of three parts that must all hold (`proc_kill.orphans_in`): it has no parent of its own left, it was born after the worker's tool, and it runs in the directory of the test that worker was running (`work_dir/<test_key>` — so a backend's `run_test` should have its tool work there). Out of reach: such a child that left the test's directory, and on other platforms any child born after the last look. A tool that died while `run_test` still returned is closed too, so the next test restarts it. Charged against the restart budget like any worker-side malfunction.
- **A cancel.** `request_cancel(test_key)` (any thread) ends the watchdog's wait with a `TestRunResult(cancelled=True)`: after `cancel_running(test_key)` and the tool's own return when that hook has a stop for the simulation alone — the worker is reused — else after the kill a timeout uses, uncharged. A run that finished anyway keeps its result. Dymola's hook writes a file named `stop` into the directory of the process NAMED dymosim (never matched in a command line: the compiler is handed a `dymosim` argument too) and has none during translation; OpenModelica's kills omc's children. A worker that overrides the watchdog (Julia) reads `self._cancel` in its own loop. Declare `Capability.CANCEL` on the backend class once a worker hears cancels.

`_detach_interface` is what makes `is_alive()` go False, and `close()` is the only thing that calls it — so a worker that keeps its handle (or its `Popen`) after a kill still reads alive, and the dispatch loop hands it the next test. That test dies against a dead process with a result carrying no `worker_killed` flag, and the restart it forces is charged against the budget. Julia hit exactly this (D110): detach the handle on every path that kills the process, not just on `close()`.

| Method / attribute | Required | Contract |
|---|---|---|
| `__init__(worker_id, config, ...)` | yes | Subclass extends with backend-specific args (e.g. `DymolaConfig`, an interface class). MUST call `super().__init__(worker_id, config)`. |
| `start()` | abstract | Bring the process up, apply startup (library loads, framework settings), hand the process's PID to `self._track_launch(pid)` the moment the process exists — before the first wait on the tool, since a start is given up from another thread and the give-up kills only what is on record (a launch call that returns the PID late gets `launch_grace`, 2 s, to do so) — and name the call in flight in `self._step`. Raise on failure. May block: `start_within` is what bounds it. |
| `startup_timeout` (class attr) | hook | Seconds a start gets when `testing.json` sets no `worker_startup_timeout`. Default `600.0`; no backend sets another. |
| `process_detail()` | hook | What the backend can say about its process when a start or a test did not come back (exit code, step, log tail), read before the kill. Default `""`. |
| `cancel_running(test_key)` | hook | Stop only the running simulation and leave the tool alive: `True` when asked and `run_test` will return on its own within `cancel_grace` (default 10 s). Default `False` — the watchdog kills the worker. |
| `is_alive()` | abstract | True iff the worker can accept another test. Usually `self._handle is not None`. |
| `run_test(test, test_key, progress=None)` | hook | The blocking single-test execution the watchdog runs on its exec thread. Required unless you override `run_test_with_timeout`. **MUST NOT** raise on test-level failures — return a `TestRunResult(success=False)`. **MAY** raise on worker-level catastrophes (RPC dropped, pipe broken); the template turns that into a `success=False` result and tears the worker down for the dispatch loop to restart. |
| `_detach_interface()` | **required iff the worker holds a handle** | Clear the live-handle attribute and return what it held; runs synchronously inside `close()`. Default `None` = nothing to shut down. |
| `_shutdown_interface(handle, grace)` | required iff `_detach_interface` returns a handle | The graceful shutdown itself (`handle.close()`, `sendExpression("quit()")`, write `{"cmd": "quit"}` + `wait`). Runs on a daemon thread abandoned after `grace`; `close()` raises `TypeError` up front if it is missing, since the daemon thread only logs what it catches. |
| `_evaluate_from_disk(test, test_key, elapsed)` | hook | Rebuild a result from on-disk artifacts after a timeout / worker exception. Default `None` = no disk evidence. |
| `_quiet()` | hook | Context manager wrapped around the watchdog's teardown calls. Default no-op; Dymola returns a suppressor for the urlopen noise its dying interface emits. |
| `kill_name_hint` (class attr) | hook | Name substring a tracked PID must still match before it is killed — the guard against a recycled PID (`"dymola"`, `"omc"`, `"julia"`). `None` disables it. |
| `timeout_kill_grace` (class attr) | hook | Graceful-shutdown budget on the *timeout* path. Default `1.0`; Dymola uses `5.0` so it can flush its open `dslog.txt`. |
| `exec_thread_prefix` (class attr) | hook | Names the exec thread `<prefix>-exec-<worker_id>`. |
| `run_test_with_timeout(...)` | template | Implemented by the base. Override wholesale only when the execution model doesn't fit "blocking call on a thread" — Julia polls a response queue against a deadline. An override still owes the shared semantics: no raise on test-level failure, `worker_killed=True` on a timeout kill, `self._kill_tracked_pids()` for the kill, and a `cancelled=True` result when `self._cancel` is set. |
| `start_within(timeout=None)` / `request_cancel(test_key)` | template | Implemented by the base. Do not override. |
| `close(grace=5.0)` | template | Implemented by the base and idempotent. Do not override. |
| `export_fmu(test, output_dir)` | iff `FMU_EXPORT` | Default raises `NotImplementedError`. |

**`PersistentBackendBase`** (subclass of `Backend`) provides the full `run_tests` template:

```
setup_before_workers → _prepare_run (progress + keys + manifest)
    → make_worker × N → start in parallel, each start bounded
    → filter live workers (if zero: fail every test with the reasons, raise)
    → dispatch with restart → close all workers → finalize progress
```

Each dispatch thread drives one `WorkerRunner` — restart a dead worker within its budget, run the test under the watchdog, keep the restart accounting. The simulation pool's threads drive the same object, which is what makes a test fail, retry and restart identically in `dstf run` and in the tool's queue.

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
| `persistent_backend_cls()` (classmethod) | `cls` | Don't override — the default returns self so the CLI doesn't recurse. |

Plus on the **batch** backend class (the one decorated with `@register("YourBackend")`):

| Hook | Default | Override when |
|---|---|---|
| `persistent_backend_cls()` (classmethod) | `None` (batch-only) | Backend ships a persistent variant. Lazy-import + return its class so the optional dep isn't pulled in during a plain batch run. |

**Inheritance order matters.** Persistent backends inherit from both `PersistentBackendBase` *and* the batch backend. The MRO must put `PersistentBackendBase` first so its `run_tests` template wins over the batch backend's per-test override:

```python
class PersistentDymolaBackend(PersistentBackendBase, DymolaBackend):
    worker_cls = DymolaWorker
    backend_label = "Dymola"
    ...
```

Worked example: `backends/openmodelica/persistent_backend.py` has the most compact of the three persistent backends' class bodies — read it as the canonical reference.

### The simulation pool

`backends/pool.py` — `SimulationPool` is `run_tests`' dispatch kept open, for a caller that queues tests while others run (the authoring tool): `pipeline.open_pool(config, *, backend, store, on_start=None, on_end=None, workers=None)` builds one over the run pipeline's per-item path.

| Call | Does |
|---|---|
| `submit(test) -> QueueItem` | Appends. `Refused` when the test is already waiting or running, or the pool is closed or stopped. |
| `update(item, test) -> bool` | Hands a waiting item the test as it stands now, keeping its place — an item runs with the last test it was given before it started; `False` once it has started. |
| `remove(item) -> bool` | Takes a waiting item out (`REMOVED`); `False` once it has started. |
| `cancel(item) -> bool` | Stops a running item (it ends `CANCELLED`); `False` when it is not running; `Refused`, with a sentence, on a backend without `Capability.CANCEL`. |
| `workers` (get / set) | How many run at once; `Config.parallel` unless given. Raised: more start as items need them. Lowered: the surplus leave as each finishes its item. |
| `waiting`, `running`, `stopped`, `can_cancel`, `path` | The queue in order, the items running, the message the pool stopped with, the capability, the per-item path. |
| `close()` | Waiting items `REMOVED`, running ones stopped (`CANCELLED`), every worker closed, every thread joined. The owner must call it. |

First in, first out. No worker exists before the first `submit`; then as many as there are items, up to `workers`. An item (`QueueItem`) carries `state`, `test_key`, `worker_id`, `run_result`, `result`, `message` and wall-clock stamps:

| `ItemState` | Meaning |
|---|---|
| `WAITING` / `RUNNING` | In the queue / on a worker. |
| `FINISHED` | The simulation ran; `result` is the result read back. |
| `FAILED` | It failed or timed out (`result` carries the failure), or no worker could start; `message` is the backend's. |
| `CANCELLED` | Stopped while running. Recorded as `last_run_status: "cancelled"` — never "failed" — and read back as no result. |
| `REMOVED` | Taken out while waiting; nothing ran, nothing was recorded. |

`on_start(item)` and `on_end(item)` are called once each, on the POOL thread that ran the item, outside the pool's lock, start before end; an item that never started gets `on_end` only when the pool itself failed it. A callback may call anything but `close()`.

When a worker cannot start, or cannot be restarted, it leaves; when none is left, the waiting items fail with the backend's own message (`No OpenModelica worker could start — worker 0: omc exited (code -9) during loadModel(Modelica); omc log: ...`) and the pool stops taking items.

**How one item runs is the backend's** — one seam, `Backend.item_runner(worker_id) -> ItemRunner`, not a name check:

- a persistent backend returns a `WorkerRunner` over one `Worker` (the default of `PersistentBackendBase`; nothing to override);
- any other backend gets the base `ItemRunner`: nothing to start, and the item goes through `run_single_test` on the pool's thread. Its cancel is `Backend.cancel_test(test_key)`, which the JSON-driver backends implement by killing the test's subprocess (`run_single_test` then returns `cancelled=True`); `PythonBackend` declares `CANCEL`, `FmpyBackend` simulates in-process and does not.

A backend that only runs whole batches (the `.mos` `DymolaBackend`) is refused when the pool is built — hand it the persistent variant.

**What happens around the run on disk** is `pipeline.ItemRun`, `run_pipeline` cut at one test: `begin(test)` keys the test and stamps it pending (`Backend.key_tests`) and wipes that test's stale state only; `end(test, test_key, run_result)` records the verdict and reads the one result back (`Backend.result_of_run`). `status.json` and the dashboard are a list run's snapshot and are not written per item; `pool.path.write_status(items)` writes them for the items given, when asked.

### Current

`Backend` ABC with five concrete implementations: `DymolaBackend`, `OpenModelicaBackend` (native Modelica), `FmpyBackend` (FMU), `JuliaBackend` (ModelingToolkit), and `PythonBackend` (arbitrary `simulate()`). The `Capability` + `DatasetType` enums and the `capabilities` / `produced_datasets` class-attribute contract gate feature availability per backend.

- `DymolaBackend` declares `{PERSISTENT_WORKERS, BATCH_FALLBACK}`. `FMU_EXPORT` and `CANCEL` sit on `PersistentDymolaBackend`, not here — batch mode has no `.mos` export path and no stop (see the note above about declaring a capability on the class that actually has it).
- `OpenModelicaBackend` and `JuliaBackend` declare `{PERSISTENT_WORKERS, BATCH_FALLBACK}`; `PersistentOpenModelicaBackend` and `PersistentJuliaBackend` declare the same pair plus `CANCEL`.
- `PythonBackend` declares `{BATCH_FALLBACK, CANCEL}` — subprocess per test, which a cancel kills; a long-lived Python worker is deferred, and FMU export isn't meaningful for arbitrary `simulate()` scripts.
- `FmpyBackend` declares `frozenset()` — no persistent worker exists, no batch fallback (FMPy *is* the Python path), no FMU export (FMPy consumes FMUs).

All five produce `{TIME_SERIES}`. The framework *does* gate on the declarations: `backends.select_backend` swaps to the persistent variant only when `PERSISTENT_WORKERS` is declared, `backends/__init__.py` rejects a registration whose flags have no matching method override, and `backends/cross_backend.py` refuses the export chain unless the primary backend declares `FMU_EXPORT`.

### Selecting a backend

`backends.select_backend(config, *, persistent: bool)` is the choice itself, and
it lives in `backends/` so callers that are not a CLI can reach it. It returns
the persistent variant when the registered class declares `PERSISTENT_WORKERS`
and that variant's `preflight` passes, and the batch class otherwise — a
`persistent=True` against a batch-only backend is a no-op, not an error, so one
CLI works across a mixed-backend config.

When the backend cannot be built here it raises `BackendUnavailableError`
(a `RuntimeError` subclass): an unregistered backend name, a constructor whose
tool is not installed, or a persistent probe that failed — the last carrying
both fixes, the install hint and `--batch`. "Cannot be built here" is a
`RuntimeError` by contract, the one `Backend.preflight` names; every backend
whose optional dependency may be missing raises that, FMPy's `fmpy` import
included, and `select_backend` translates it. The translation is deliberately
narrow — it wraps exactly `get_backend_class(config)` and exactly the
`cls(config)` call — so a genuine bug inside a constructor still reaches you as its own
traceback.

`cli.run_cmds._get_backend` is the CLI's wrapper over it and nothing more: it
prints the message and exits 1, where a caller with a UI puts the same message
on the job that asked for the run. `Session.open` builds through
`select_backend(config, persistent=False)` — the batch class, since the
Session's backend reads results rather than running them — and turns
`BackendUnavailableError` into `ConfigError`, so `dstf author` on a machine
that cannot build the backend prints one `Config error: ...` line and exits 2.

### The run as a library

`pipeline.run_pipeline(config, tests, *, backend, store, merge=False,
candidates=None) -> RunOutcome` is the run itself, without a CLI around it:
the ref-ID mapping, the stale-state wipe for the tests in scope, the live
dashboard's first paint, `backend.run_tests`, the cross-backend chains, the
`merge` scope widening and the result read. It never prints, never exits the
process, never opens a browser and never prompts; a backend that cannot start
raises, and the caller decides whether that is an exit code or a red job panel.
`cmd_run` is its CLI wrapper — selection, the outcome's notices printed,
`--accept` / `-i` / the report — and the authoring tool's Rerun capability is
the other caller.

`RunOutcome` carries `manifests`, `scope_tests`, `notices` (the cross-backend
chain's lines, for the caller to show however it shows things) and `results`,
which is keyed by exactly the models in `scope_tests` — wider than what just
ran when `merge` is set, never wider than the scope, whatever else the work
dir's accumulated manifest holds from earlier runs.

The run's real product is on disk, not in the return: `batch_manifest.json`,
`status.json`, `dashboard.html`, `reference_manifest.json` and the per-test sim
dirs. The authoring Session reads a finished run back through
`backend.read_last_results`, so a caller that drops the returned outcome has
lost nothing the next read cannot recover.

Nothing in `backends/` narrates a run to the console unless it is asked to:
`Backend.quiet` (instance attribute, default `False`) suppresses the run header,
the progress bar, the summary, the read phase and the worker-lifecycle lines.
The CLI leaves it false; the authoring tool sets it, because its backend's
console belongs to nobody. **A new run-narration `print` in a backend is gated
on it** — inside one of the `base.py` narration helpers, which return when
`quiet` is set, or under an `if not self.quiet:` of its own. Logging is not
narration and is not gated.

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

**Contract**: each Dataset type has a well-defined numpy-friendly shape. Metrics declare which Dataset types they accept (`MetricDescriptor.accepted_datasets`). Backends declare which they produce (`produced_datasets`). An incompatible leaf hard-fails at evaluation time with a diagnostic naming both kinds — the kind is only known once a backend has produced data, so there is no earlier config-time hook.

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
1. `accepted_datasets` declares which Dataset types the metric consumes. A
   leaf handed an incompatible kind hard-fails **at evaluation time** with a
   diagnostic naming both kinds (there is no earlier config-time hook today —
   the kind is only known once a backend has produced the data).
2. `compare` is *pure* — no I/O, no mutation of inputs, no global state. Deterministic given inputs.
3. `diagnostics` must be JSON-serializable (travels to reports + reference files).
4. Metric-specific configuration is typed (frozen dataclass); its field
   metadata (`label`/`help`) drives the exported JSON-Schema and the authoring
   tool's auto-derived forms. Both read the same `derive_schema` in
   `comparison/mode_schema.py`. A tolerance that takes two sides is a field
   typed `Sides` (item 11).
5. `score_direction` declares which way a better score runs (`"lower"` for
   error-like, `"higher"` for coverage-like). The validator uses it to reject
   `weighted` nodes mixing directions.
6. `accepts_quantity` (default `False`) declares that the metric's `quantity`
   param names *what data it scores*. Only for such a metric does a string
   `quantity` make the evaluator resolve a stored scalar from the run's
   statistics / diagnostics instead of a trajectory, and only then may
   `variable` be omitted in the spec. Leave it `False` and an unrecognized
   `quantity` stays an inert param — a trajectory metric keeps scoring
   trajectories.
7. `edit_handle` (default `None`) is the inverse of the metric's
   `kind="handle"` geometry: what the authoring tool calls when a user drags
   one of its handles on the plot (`comparison/handles.py`). It takes a
   `HandleEdit` — `leaf` (the raw leaf as `metrics` holds it; never mutate
   it), `local` (the handle's id without its leaf path, e.g.
   `"tube/upper/3"`), `x` and `y` (the dragged position in data coordinates,
   `None` for an axis that did not move), `reference` (the primary
   baseline's `(time, values)` at full resolution, unwindowed, or `None`),
   `target` (the number a tolerance side is measured from where the leaf's
   own geometry holds it — a `scalar` leaf's baseline scalar, read off its
   `scalar/reference` line — else `None`) and `span` (the ACTUAL series'
   first and last time as the scorer received it, sliced to the leaf's
   window, or `None`: what bounds a time that is scored against the run) —
   and returns
   the new raw leaf, or raises `HandleEditError` naming the handle and the
   reason for a drag the leaf cannot take — with the reason alone as its
   `reason` (`handles._refuse` builds both), which is what the tool shows in
   the open criterion's editor. The tool stages what comes back as
   ONE `replace` of the whole leaf, so an editor rewrites the leaf, never a
   field path, and a leaf equal to the input stages nothing. The builtin
   editors round every float they write to 12 significant digits
   (`handles._written`), so drag arithmetic never lands float noise in the
   author's spec file. Leave it `None`
   and a drag of the metric's handle markers and lines is refused ("has no
   on-plot handles"); the builtin `nrmse` does. Only a `Marker` or an
   `HLine` of `kind="handle"`, and a `Marker` or a `VLine` of `kind="grip"`
   (item 12), reaches the editor: nothing else on the plot — a window, a
   box, a result line — takes the pointer in the page, and a drag of one
   that arrives all the same stages nothing. A grip moves on ONE axis: its
   editor gets the released position on that axis and the grip's own
   resolved position on the other, never something to write there. The
   builtin editors are the worked examples: `tube`, `points` and
   `dominant-frequency` move a list entry, `range` and `scalar` a bound
   (`scalar/min|max`), and all but `range` a tolerance's side — a `scalar`'s
   by its lines `scalar/upper|lower`, `kind="handle"`, the others by their
   grips; `event-timing` has only those. `handles._set_side` is the one rule
   for writing a dragged side: a number stays a number, a pair changes that
   side alone, a side dragged across its target is refused.
8. `seed` (default `None`) gives a new leaf its first values, from the data
   it will score: the authoring tool calls it when a criterion of this
   metric is added and when a criterion's metric is switched to it
   (`comparison/seeds.py`). It takes a `SeedData` — `ref` and `act`, each a
   `(time, values)` pair or `None`, already cut to the criterion's window
   when it has one (`SeedData.series()` is the reference, else the actual,
   whichever first has a finite value) — and returns the params to add to
   `{"metric", "variable"}`, or `{}` when there is nothing to seed from or
   nothing worth seeding: `dominant-frequency` leaves a peak in the lowest
   three FFT bins unseeded, a trend's leakage rather than a frequency. It
   is pure, like `compare`. Leave it `None` for a metric valid bare: the
   builtins `nrmse`, `points` and `event-timing` do. The builtin seeds are
   the worked examples: `range` brackets the data, `tube` a band around the
   reference, `scalar` the final value and `dominant-frequency` the
   strongest peak. A bound or a width is rounded AWAY from the data at the
   leading digit of the margin it leaves, so the spec file reads short; a
   seeded leaf passes on the data it came from — a seed that reads the
   actual, as `scalar` does when the reference has no finite sample in the
   window, does not score against the baseline.
9. A config field holding a list of row dicts declares `columns` in its
   metadata — `(key, type)` pairs, one per row key, where `type` is
   `"float"` (a key the metric indexes: required, a blank cell is never staged),
   `"float?"` (read with `.get`: optional, a blank cell omits the key),
   `"sides"` / `"sides?"` (a tolerance — a number or a `{"lower", "upper"}`
   pair — required or optional likewise, drawn as two fields and a link
   toggle) or a tuple of choices whose first is the metric's default. A
   `sides` entry may carry a third element, what its two sides are called,
   lower first: `("time_tolerance", "sides?", ("before", "after"))`; without
   one they read `−` / `+`. Read an entry by index, never by unpacking two.
   The authoring tool's
   Edit on plot tab draws a table from it, with add and remove rows. The table
   holds a blank required cell locally — marked on the cell, nothing submitted
   until it is filled — and the capability's `act` still refuses one when called
   directly. `columns`
   is read from `dataclasses.fields`, not through `derive_schema`, so it
   never reaches `export-schema`. A list field without `columns` gets no
   table; Edit criteria shows every list field read-only either way.
10. `drawn_by_default` (default `True`) says whether the authoring tool's
    Test page draws a closed criterion of this metric on its plot before the
    swatch on its row is touched. Set it `False` for a metric whose drawing
    crosses the whole plot — the builtins `range` and `scalar` draw bounds
    from edge to edge, which would cover every plot they sit on. It is only
    the default: a failing criterion draws whatever it says, the open one
    always draws, and the swatch overrides it either way. The report does
    not read it.
11. A tolerance is a number or a lower/upper pair (D147), and travels as
    `comparison.sides.Sides(lower, upper)`. `build` is
    `Callable[[dict, Sides], ComparisonMode]`: its second argument is the
    leaf's resolved tolerance as a `Sides`, a number in the spec being both
    sides — a metric with one ceiling refuses `not tolerance.equal` with a
    `ValueError` (the leaf's config error) and reads `tolerance.upper`, as
    `nrmse` does. Read any other tolerance field of your own through
    `parse_sides(value, "<field name>")`, the one reader: its `ValueError`
    names the field and is that leaf's config error when raised from `build`
    or the mode's `__init__`, on both scoring paths; raised later, from
    `compare`, it fails that leaf or variable all the same. Type the config
    field `Sides` and
    `derive_schema` classifies it `"sides"`: the form draws two fields and
    a link toggle (`FieldSpec.side_labels` names them, from the field's
    `side_labels` metadata, `−` / `+` by default), and `export-schema`
    writes `oneOf` a number or an object with exactly `lower` and `upper`.
    The meaning is the metric's to keep: with `d = actual − target`, pass
    iff `−lower ≤ d ≤ upper`. `VariableComparison.tolerance_used` is a
    `Sides`: the constructor takes a number too, but a metric that ASSIGNS
    it assigns a `Sides`. Where it leaves Python as data, write
    `as_written(sides)` (the number when the sides are equal, else the
    pair); in a sentence, `sides_text(sides)`.
12. Geometry of `kind="grip"` is the resolved position of one SIDE of a
    tolerance: a `Marker` on a value's or a time's edge, a `VLine` on the
    edge of a window that spans its panel. Its local id is
    `<family>/<edge>/<key>`, the edge one of `upper` / `lower` (a value: it
    moves on y), `before` / `after` or `early` / `late` (a time: on x; a
    `VLine` always moves on x) — `points/upper/3`, `events/late/0`,
    `tube/lower/const` — and its group is the id without the edge
    (`points/3`), which is also how the handle it stands around reads
    (`points/target/3`). A grip is never a trace and never drawn in a
    figure that is not editable, so emitting one changes no report page; in
    the tool it is an arrow just outside its edge, drawn for the open
    criterion only and shown while the pointer is near its group. A side
    that should be drawn on the report too is a `kind="handle"` line
    instead, as the scalar's `scalar/upper` and `scalar/lower` are.

### Currently registered

- **`nrmse`** — piecewise NRMSE with event-boundary handling. `NrmseMode`.
- **`tube`** — envelope comparison, three width modes. `TubeMode`.
- **`points`** — declared-checkpoint comparison; empty list ⇒ final-value-only, non-empty ⇒ multi-point with abs/rel y-tolerance + x-tolerance box. `PointsMode`. D84.
- **`range`** — signal-only bounds check (no baseline needed). `RangeMode`. D53.
- **`event-timing`** — compare event instants via duplicate-time detection. `EventTimingMode`. D62.
- **`dominant-frequency`** — FFT peak comparison. `DominantFrequencyMode`. D62.
- **`scalar`** — score one number: a reduction of the leaf's variable (`max` / `min` / `final` / `integral`, plus time-weighted `mean` and arithmetic `sample_mean`) or a stored statistic addressed by dotted path, against a baseline scalar (abs/rel tolerance) or declared bounds. The only metric declaring `accepts_quantity` — its `quantity` param names the data it scores. Accepts the `scalars` dataset kind as well as time series. `ScalarMode`.
- **`x-tolerance`** *(planned)* — pyfunnel x+y funnel comparison.
- **`fréchet`** / **`iso-18571`** *(future)* — shape-sensitive metrics.
- **`ks-distribution`** *(future)* — stochastic regression.

### User metrics (implemented — `dstf.comparison.registry`)

A user registers a metric by describing it once; every framework surface
(spec parsing, evaluation, `export-schema`, and the authoring tool's forms)
derives from the descriptor:

```python
from dataclasses import dataclass, field
from dstf.comparison.modes import ComparisonMode
from dstf.comparison.registry import MetricDescriptor, register_metric

@dataclass(frozen=True)
class PowerBalanceConfig:
    tolerance_watts: float = field(
        default=5.0, metadata={"label": "Tolerance (W)", "help": "..."}
    )

class PowerBalanceMode(ComparisonMode):
    def __init__(self, config): ...
    @property
    def name(self): return "power-balance"
    def compare(self, ref_time, ref_values, act_time, act_values): ...

register_metric(MetricDescriptor(
    name="power-balance",
    config_cls=PowerBalanceConfig,
    build=lambda override, tol: PowerBalanceMode(
        PowerBalanceConfig(tolerance_watts=override.get("tolerance_watts", 5.0))
    ),
    score_direction="lower",
))
```

And references it in `test_spec.json` like any built-in:

```json
{"metric": "power-balance", "variable": "P_net", "tolerance_watts": 5.0}
```

The framework requires no change; `tests/test_metric_registry.py` proves the
full round-trip with a toy metric. One boundary to know: the interactive
report's **live** re-scoring and its add-leaf dropdown are implemented in
the template JS, which carries its own scorer table — a Python-registered
metric evaluates, renders, and exports normally but shows the
"CLI-authoritative" badge (its pill updates on the next `dstf run`, exactly
like the built-in `event-timing`).

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

Pure function. Diagnostics must preserve enough of the children's diagnostics that a failing branch can be rendered in the report. Combinators register via `dstf.comparison.registry.register_combinator` (a `CombinatorDescriptor`: name + a `CombinatorSpec -> Combinator` builder). One rule the validator enforces on `weighted`: its leaf children must share a `score_direction` — summing lower-is-better and higher-is-better scores against one threshold is meaningless.

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

## Capability contract (authoring tier)

The authoring tool is a plug-in surface too. A **capability** is one thing an
author can do to a project — edit a criterion, edit the tree, accept results,
rerun a selection, drag a tube's control points — and it registers through the
same `dstf.plugins` entry-point group a metric does. A capability decides
whether it is available, builds its own controls, and returns an *outcome*; it
applies nothing itself, so the session stays the single place where a spec is
mutated, a store is written, or a job is launched.

The contract lives in `dstf.authoring.capabilities`: the `Capability` protocol
(`name`, `label`, `placements`, and `available` / `caption` / `panel` / `act`),
the `Placement` enum (`SUITE`, `TEST`, `VARIABLE`, `CRITERION`, `NODE`,
`STATISTIC`, `STATISTIC_CRITERION` — where the capability attaches; a capability
names a frozenset of them), and the
five outcomes `act` may return — `Patch` (ops for the Session to stage),
`StoreWrite`, `Job`, `Artifact`, `OpenProject` — or `None`, or a sequence of
them. `Job` and `StoreWrite` each carry `models: frozenset[str]`, the models
they touch: the Session locks a job's models while it runs and a locked model
refuses a `StoreWrite`. Every outcome is keyword-only, so a field added later
cannot silently take a positional argument's place. `caption(view)` is the
button's label — object, effect and count, `plural()` for the noun — and
`available` the sentence beside it when it is greyed. The view's `selection`
is the page's that built it (D129): `SessionView(session, selection)`.

**The placements** (D129, D131). Each one says how the shell draws a capability
and what the view it hands over carries; a capability chooses the placements,
never the way it is drawn there — save its optional `emphasis`, which says per
situation of the Test page whether it is the next action there (drawn filled)
or belongs in the status strip's ⋮ menu.

| Placement | Where | How it is drawn | `selection` carries | `panel.figure` |
|---|---|---|---|---|
| `SUITE` | the Suite page's batch bar | a button with the `caption`, panel in a dialog | `tests` (the checked tests, possibly empty) | raises on `on` |
| `TEST` | the Test page's status strip, under its header row | a button with the `caption` (filled, quiet or in the ⋮ as its `emphasis` says), panel in a dialog | `tests` — exactly the one test of the page | raises on `on` |
| `VARIABLE` | a plot card's criteria header, for the plot's first tracked series | inline: the panel alone at the header's right end (Edit tree's `+`), its forms a row under the header — no dialog | `tests` (one), `variable`, and `texts` (the page's scope, so the panel reads the variable as the page does) | that card's plot |
| `CRITERION` | the open criterion's editor, below its row | inline: the panel alone, no button, no heading | `tests` (one), `variable`, `leaf_path` | the plot the row sits under |
| `NODE` | a tree node's row, and a criteria row under a plot | inline: the panel alone, no button, no heading | `tests` (one), `node_path` (a leaf OR a combinator), and `variable` on a criteria row | raises on `on` |
| `STATISTIC` | the end of a Statistics-card row no criterion reads (D143) | inline: the panel alone (Edit tree's `+`), no button, no heading | `tests` (one), `quantity` (the row's quantity path, e.g. `translation.nonlinear_count`) | raises on `on` |
| `STATISTIC_CRITERION` | the editor of a criterion scored through a statistic, under its Statistics row (D143) | inline: the panel alone, no button, no heading — CRITERION's editor with no plot, so a capability that needs one is not offered there | `tests` (one), `leaf_path` | raises on `on` |

An inline panel (`VARIABLE`, `CRITERION`, `NODE`, `STATISTIC`,
`STATISTIC_CRITERION`) draws no heading and no button
of its own frame: the row it sits in already names it — the criterion's row above
the editor, the plot's "Criteria" header — and a second name there makes one
editor read as two panels. Its `caption` is never read. Where a
button placement greys its button and puts `available`'s sentence beside it, an
inline placement shows that sentence *in place of the panel*, so the control
keeps its place in the row and says why it cannot be used.

`act` returning `None` means nothing happened: the host applies nothing, rebuilds
no panel and leaves an open dialog open — there is nothing to close over. It is a
real answer, not an oversight (every plot sends one `{"autosize": true}` relayout
when it mounts, and a subscribing panel answers that with `None`), so a capability
that returns `None` on purpose says why in its own panel.

`StoreWrite.run` returns `str | None`: the one line the job panel shows, saying
what the write did — what it stored and what it refused, with the reason. A
partial write has no other way to report its skips, and raising after some
baselines were already stored would say something false. The Session appends
that line to its notices and emits `job` with `state="written"`; returning
`None` says there is nothing to report and appends nothing.

The view carries the job panel's other readers: `notices`, the panel's lines
oldest first — what each store write reported, plus the open-time warning where
there was one; `last_job`, the job that ran last in this project whatever its
outcome; and on a `RunningJob`, `started_wall` — epoch seconds sampled before
the worker thread starts. A `status.json` whose `start_wall` is older than that
belongs to the previous run, and the panel does not show it as this job's
progress. `notices`, `last_job` and `artifacts` are per project: opening another
one resets all three at the commit point, so one library's panel is never shown
under another's, and an open that fails leaves the live project's as it was.

`backends.progress.read_status(work_dir)` is the one reader of `status.json` —
every reader comes through it, and it never raises, because a UI timer calls
it.

Register with `register_capability(cap)` from a module the `dstf.plugins`
entry-point group loads, exactly as a metric calls `register_metric`; a duplicate
name is an error rather than an import-order race.

A capability receives a `SessionView` — readers only — and applies nothing
itself: the Session is the single place a spec is mutated, a store is written or
a job is launched. The view carries what a panel draws from: `comparisons`,
`figure(model_id, variable)`, `tree_view(model_id)` — the read-only tree the
report page and the shell both show, built by the report's own function — and
`tree(model_id)`, a copy of the raw tree a `Patch` edits (the entry's `metrics`
block, else the tree a rescore would materialize for a test the flat path
scored). `rescore` is the one reader that writes — it stages that
materialization — so `available` must not call it.

**Building a panel.** `Panel` is a container the capability owns entirely: it
builds its controls into the slot its host owns — a dialog's, or an inline
host's column — and may subscribe to the plot it is drawn beside through
`panel.figure.on(kind, handler)`, whose handler receives the primitive's id and
a payload. When a control fires, the capability calls `panel.submit(**inputs)`:
the shell calls `act(view, **inputs)` and hands what comes back to
`Session.apply`. A capability never calls `act` or `apply` itself, and an
exception from either is shown with the panel — above an inline one, in the
dialog of a button placement — with the Session unchanged; the next successful
submit hides it in place, without rebuilding the panel, so the field being typed
in survives. `submit` returns whether it went through: `False` when `act` or the
apply raised. Import the UI framework inside `panel`, not at module level, so the
module stays importable without the `[author]` extra — the built-ins do.

**What a panel shows that the spec does not hold.** A panel is rebuilt by edits
it did not make — a drag on the plot rebuilds the whole criterion editor, another
panel's submit rebuilds this one — and is never told. Built from the view alone,
whatever its controls held beside the spec is gone: a required cell cleared to be
retyped comes back filled. `panel.kept` is a dict of the panel's own that
outlives it: write there as a control becomes such a thing, read it back when the
panel is built. The host hands the same dict to every build of that capability's
panel for the same node of the same test, re-keys it when a tree edit moves the
node, and drops it — rebuilding the panel on an empty one — with the node and
when an edit that is not this tab's lands on the test (a Discard, a Save, another
tab's). Record beside each entry what the spec held under it, and ignore an entry
whose spec value has since moved. The record is the browser tab's, not the
page's: it outlives a switch of test and a visit to the Suite page, and is there
when the criterion is opened again in that tab. Only the criterion editor under
a plot keeps one (Edit on plot's table records its blank and half-typed cells
there, Edit criteria's form a tolerance with one side typed). Every other host hands a fresh dict to every build — a dialog, a row,
and the two other criterion editors too: the one under a row of the criteria
tree and the one under a row of the Statistics card — so a panel placed at
several of these must work with a dict that is empty each time.

**A form row.** `panel.row(title, build)` opens a form below the row the panel
is drawn in — how Edit tree's add, wrap and edit forms open under a tree node's
row or a criteria row (D133), and its add form under a plot's criteria header. `build(close)` draws the form's content; `close`
takes the row away. Call `close` after a `submit` that returned `True`, never
before it: a refused act then leaves the form open with what was typed, and its
error shows under the row. One thing is open at a time on a page, so opening a
row closes any other row and the open criterion's editor, and opening an editor
closes the row. While the row is open the page calls `build` again, into an
emptied row, when an edit staged elsewhere moves the node the panel is for, so a
form never shows a value its node no longer has; build its fields from the view
each time. The row also closes when its host is rebuilt, when its row is hidden
and when its node is gone. In a dialog (a button placement) the form is drawn
in the dialog, below the panel; the criterion editor has no row, and `row`
raises there. `panel.reveal(leaf_path)` asks the page to open that criterion's
editor once the page has drawn it — after an add, the new criterion — on the
plot the panel's row is on when that plot draws the criterion's variable, else
on the first plot that does, and to scroll it just into view. It records the
path only when that node exists, so call it after the `submit` that made it;
where no page reveals criteria (a dialog, the criterion editor) it does nothing.

**A lead place.** `panel.lead(build)` draws `build()` in the node's lead place:
a tree row's, right after its status pill, where the node's kind is read — how
Edit tree draws a combinator's kind as a dropdown there. The place is emptied
before each draw and with the panel's host; while it holds something the row's
text leaves the kind out (`3 criteria`, not `and · 3 criteria`), and a host that
draws nothing there — Edit tree under a job's lock — leaves the row reading its
kind. A row without a lead place (a leaf's, a criteria row's, a plot's header,
a dialog) raises, as `row` does where there is no row.

**Build from the view, every time.** An inline host rebuilds every capability's
panel in its slot whenever what they show can have moved — after a staged edit,
Discard, Save, Reload or open, once the comparisons are rescored — except the
acting capability's own panel while an act from its own controls runs, so a
field being typed in survives; when that act came from the figure hook (a drag,
during which nothing is being typed) the acting panel is rebuilt too. A dialog's
panel is not rebuilt while the dialog is open: a confirm list belongs to the
moment of the click, and an act that applied something closes the dialog. When
the set of capabilities placed there changes, or an `available` answer moves —
a job's lock comes and goes without the tree changing shape — the host rebuilds
them all. So a panel must read what it shows from the view on each build, never
from state it kept from an earlier one: a panel that captured its leaf when
first built would show — and on its next change re-stage — values another
capability, a Discard or a Reload has since replaced. State a panel does keep
between its own acts (a table's rows while rows are added) is fine, because the
next rebuild reads the Session again. A panel's hook handlers go with it: its
host registers them under that capability's own owner key and clears exactly
those when it rebuilds the panel, when an inline host is emptied, and — for a
button placement — however its dialog closes, so a panel nobody can see never
stages an edit from a drag.

**The figure hook's payloads.** `panel.figure.on(kind, handler)` calls
`handler(primitive_id, payload)` for three kinds:

- `plotly_click` / `plotly_selected` — the id of the first point whose trace
  `meta.kind` is `"handle"`, else of the first point (its `customdata`, then
  `meta.id`, then `curveNumber`), with the event payload. Under the figure's
  x-unified hover a click lists every trace at that x, so a handle is not
  necessarily the first point.
- `plotly_relayout` that moved shapes — once per shape, ascending by index.
  Plotly's payload names the shape only by index; the shell resolves the id
  from the rendered figure's `layout.shapes[N].name` (`""` when it cannot) and
  hands over that shape's keys without their `shapes[N].` prefix, in data
  coordinates: `{"y0": 6.0, "y1": 6.0}` for a line dragged whole, one end's
  keys for a line dragged by an end, `{"xanchor": 4.0, "yanchor": 2.99}` for a
  handle ring or a grip's arrow moved. Both are pixel-sized `path` shapes,
  which move from wherever they are pressed and never resize; read one only
  by `xanchor` / `yanchor`, and a grip only by the anchor on its own axis.
  On the Test page the release passes through the page's `grips.js` first:
  an axis the pointer travelled under 8 px along is sent as `None`
  (unchanged), and a position let go outside the plot area is held to the
  axis range the plot had when it was pressed.
  The shell interprets no positions; the capability does, from the handle's
  primitive type. After the handlers run, the shell re-sends the figure
  whether or not anything was staged, so a drag that changed nothing snaps
  back.
- Any other relayout (a zoom) — once, with id `""` and the raw payload.

In the tool's figure each `kind="handle"` Marker of a leaf named in
`rings_for` gets a ring shape named with its id, and each `kind="grip"`
primitive of such a leaf an arrow shape named with its id — on the Test page
that is the open criterion alone (D131); lines, boxes and windows keep their
own names. plotly.js makes every shape of an editable figure draggable and has
no working per-shape switch, so the figure says which of its shapes take the
pointer, in `layout.meta.dstf` (`figures/plotly.py::POINTER`):

- `grips` — `{shape name: {"group", "axis", "label"}}` per grip arrow: the
  group it shows with, the axis it moves on (`"x"` / `"y"`), its tooltip;
- `targets` — `{ring name: group}` for each ring a group's grips stand
  around;
- `draggable` — the list of shape names an editor moves: the ringed leaves'
  rings, grips and `kind="handle"` lines.

The Test page's `grips.js` makes every shape the map does not name draggable
inert — and a line it does name, while that line is off the plot — and shows
a group's arrows only near it; a figure with no map is left as Plotly draws
it. The map rides in the figure so that it is always the map of the figure
the browser holds. The
handler semantics are the hook's own, not the DOM's: each shape is dispatched
from a snapshot of the handlers taken when its dispatch starts; a handler
registered since is not called for it, one removed since (its panel rebuilt) is
skipped, checked by identity; a handler registered twice is called twice; a
handler that raises ends the relayout — later handlers and shapes are not
dispatched — and the snap-back still runs. A panel rebuilt by an act its own
drag triggered therefore registers its new handlers immediately and is not
called again for that event.

**Forms.** A panel over a typed config need not hand-build its controls.
`derive_schema(config_cls, mode=...)` (`comparison/mode_schema.py`) describes
the dataclass, and `render_form(schema, values, on_change)`
(`authoring/forms.py`) turns each field into one NiceGUI control, calling
`on_change` with the full values dict after every edit. A dataclass default is
shown as the control's placeholder and never written; `ui_min` / `ui_max`
validate rather than clamp; `passthrough` fields are shown read-only; a
`sides` field is `forms.sides_control` — two number fields and a link toggle
whose state is the shape of the field's value: a number while linked, a
`{"lower", "upper"}` pair while not; a pair with one side typed changes
nothing until the other is. The same
`derive_schema` feeds `dstf export-schema`'s JSON-Schema emitter.

The five shipped capabilities live in `authoring/builtin/` and register through
this same call — Edit criteria and Edit tree return a `Patch`, Accept a
`StoreWrite` over the shared accept function, Rerun a `Job` that builds its own
backend through `select_backend` and runs it through `run_pipeline`, and Edit on
plot a `Patch` from a metric's `edit_handle` or a table's rows — the one
builtin client of the figure hook.

§4.6 and §4.7 of [the two-tier architecture
design](superpowers/specs/2026-09-08-two-tier-architecture-design.md) have the
full specification; [usage.md](usage.md#the-authoring-tool) describes the tool
they build.

---

## Cross-cutting guarantees

Every plug-in, regardless of layer, must honor:

1. **Determinism** — same inputs produce the same outputs. No wall-clock, no PID, no randomness without an explicit seed.
2. **JSON-serializable configuration** — all plug-in config must round-trip through `test_spec.json` and the reference JSON.
3. **Clear error messages on misuse** — "Backend `Foo` does not produce `Events`; metric `event-timing` requires it" is a framework-level check, not a runtime crash.
4. **No framework-core edits** — if adding a plug-in requires editing the `cli/` package, `config.py`, or anything outside `backends/`, `metrics/`, `combinators/`, `discovery/`, the abstraction is wrong. File an issue before working around it.
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

Third-party plug-ins live in separate packages and register via the `dstf.plugins` entry-point group (see [Shipping a plug-in in your own package](#shipping-a-plug-in-in-your-own-package)). The same layout applies there.
