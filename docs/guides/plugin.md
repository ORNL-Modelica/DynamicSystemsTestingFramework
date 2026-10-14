# Write a backend plug-in

A plug-in adds to dstf without changing it: a backend for another simulator, a
comparison mode, a group of the criteria tree, a recognizer. It is a Python
package that dstf finds when it is installed in the same environment. This
page builds a backend, with the example the repository ships and tests,
`examples/plugins/DecayBackend`.

The interfaces a plug-in uses are on the [Python API](../reference/api.md)
page. They are provisional while dstf is in beta: a minor version may change
them, and the [changelog](../changelog.md) says when.

## How dstf finds a plug-in

A plug-in package declares an entry point in the `dstf.plugins` group of its
`pyproject.toml`:

```{literalinclude} ../../examples/plugins/DecayBackend/pyproject.toml
:language: toml
```

Before any command runs, dstf loads every entry point of that group. One that
names a module is imported, and the registrations in it run; one that names a
function is called with no arguments. A plug-in that fails to load stops the
command with a config error that names its entry point: a mode that silently
failed to register would change how tests are scored.

How each kind registers:

| Kind | Registered with |
|---|---|
| backend | the class decorator `dstf.backends.register("Name")` |
| comparison mode | `dstf.comparison.registry.register_metric(MetricDescriptor(...))` |
| group of the criteria tree | `dstf.comparison.registry.register_combinator(CombinatorDescriptor(...))` |
| recognizer | `dstf.discovery.recognizer.register(recognizer)`, an instance of a `Recognizer` subclass |

A recognizer that only matches a naming convention needs no plug-in at all: it
can be declared in `testing.json` ({doc}`../reference/recognizers`).

## The example backend

The example's model is a decay, `y(t) = exp(-rate · t)`, computed in dstf's
own Python, so the backend has nothing to install and nothing to start:

```{literalinclude} ../../examples/plugins/DecayBackend/dstf_decay.py
:language: python
```

What each part does:

- **`@register("Decay")`** makes `"backend": "Decay"` in `testing.json` and
  `--backend Decay` name this class. An entry called `Decay 2` is a Decay
  too.
- **`source_types`** are the `source_type` values the backend runs. A project
  that names no backend runs with the first installed one that runs its
  source type.
- **`source_suffix`**, left at None here, is for a backend whose test runs a
  file its entry names under `source` (Python's `".py"`): `dstf spec add`
  then requires `--source`.
- **`settings`** are the keys of the backend's block in `testing.json`'s
  `backends`, each a `Setting` with its JSON schema, default and description.
  Extend `Backend.settings`, which holds `executables`. A key the block holds
  and the table does not is a config error, and the reference page and the
  published schema list the table. A setting that is a path says so, and the
  backend receives it absolute.
- **`locate`** is the only place the backend's install is looked for, and
  `dstf check` prints what it returns. The default finds one executable: the
  ones the block's `executables` (or `--backend-path`) names, then the names
  in `binaries` on `PATH`. A backend overrides it to read an environment
  variable, to search install folders, or — as here — when there is nothing to
  find. `diagnose` adds to what `dstf check` says; the default prints the
  located parts.
- **`run_single_test`** runs one test into its directory of the work
  directory, and says whether the simulation succeeded. The framework's
  `run_tests` calls it for each test, `--parallel` at a time, and records the
  run; a backend with a run model of its own overrides `run_tests` instead.
  A failed simulation is a result with `success=False`, not an exception.
- **`read_result`** reads that directory back into a `TestResult`: the time
  base and one `VariableResult` per tracked variable. A variable's `name` is
  the one its result file gives it; `expression` is only for a source that
  wrote another text for the same variable, as a Modelica `UnitTests`
  component does.

## Capabilities

`capabilities` declares what the backend can do beyond running a test, and
each is a promise: `register` refuses a class that declares one without the
method it needs.

| Capability | The method it needs |
|---|---|
| `FMU_EXPORT` | `export_fmu(test, output_dir)` |
| `LIVE_RESULTS` | `live_result_path(test_dir)`, the file that grows while a test runs |
| `CANCEL` | `cancel_test(test_key)`; in the class whose workers stay open, the `Worker` stops the test |
| `PERSISTENT_WORKERS` | `persistent_backend_cls()`, the class whose workers stay open |
| `BATCH_FALLBACK` | `run_single_test` or `run_tests`: a run with no worker that stays open |

`produced_datasets` is a declaration only. What is checked is the data a
result holds: a criterion whose mode cannot score it fails, with a reason.

A simulator that is slow to start and quick to run a test pays for workers
that stay open: subclass `PersistentBackendBase` and a `Worker`, whose
methods and hooks are on the [API page](../reference/api.md#backends-that-stay-open).
The base class runs the workers, their timeouts and their restarts.

A backend that prints during a run checks `quiet` first: a caller with no
console sets it.

## Try it

Install the example beside dstf, then run its project twice — once to store
the baseline, once to compare with it:

```console
$ uv tool install dynamic-systems-testing-framework --with ./examples/plugins/DecayBackend
$ dstf --config examples/plugins/DecayBackend/project/testing.json run --accept
$ dstf --config examples/plugins/DecayBackend/project/testing.json run
```

(With pipx: `pipx inject dynamic-systems-testing-framework ./examples/plugins/DecayBackend`.)
`dstf check Decay` now lists it, and `dstf spec schema testing` includes its
`rate` setting.

## What every plug-in owes

- **The same inputs give the same result**: no clock, no random number
  without a fixed seed, in anything that is scored.
- **Its configuration is JSON**, so that it travels in `testing.json`,
  `test_spec.json` and the stored baselines.
- **A mistake is an error that says what is wrong**, where it is made — not a
  crash, and not a silent fallback.
- **A test**: the example's is `tests/test_plugin_example.py`, which installs
  it the way pip would and runs its project.

A plug-in that cannot be written without changing dstf itself has found a gap
in these interfaces: open an issue.
