# Python API

:::{admonition} Provisional
:class: warning

These interfaces are provisional in 1.x: they may change between minor
versions, and the changelog will say so. Anything that is not on this page is
internal and may change at any time. A run's results are read from the files
it writes, such as `verdicts.json`, not from Python objects.
:::

```{eval-rst}
.. autofunction:: dstf.load_plugins
```

## Backends

```{eval-rst}
.. autofunction:: dstf.backends.register
```

```{eval-rst}
.. autoclass:: dstf.backends.base.Backend
   :members: __init__, locate, settings_of, executable_of, diagnose, run_single_test, read_result, run_tests, persistent_backend_cls, preflight, describe_tool_version, export_fmu, cancel_test, live_result_path, result_names, result_series, label
```

```{eval-rst}
.. autoclass:: dstf.backends.types.Capability
```

```{eval-rst}
.. autoclass:: dstf.backends.types.DatasetType
```

```{eval-rst}
.. autoclass:: dstf.config_shape.Setting
```

```{eval-rst}
.. autodata:: dstf.config_shape.FILE
```

```{eval-rst}
.. autodata:: dstf.config_shape.PROGRAM
```

```{eval-rst}
.. autofunction:: dstf.backends.locate.find_executable
```

```{eval-rst}
.. autoclass:: dstf.backends.locate.Located
```

```{eval-rst}
.. autoclass:: dstf.backends.locate.Found
```

```{eval-rst}
.. autoclass:: dstf.backends.locate.Rule
```

```{eval-rst}
.. autodata:: dstf.backends.locate.EXECUTABLE
```

The kinds of {class}`~dstf.backends.locate.Rule`, in the order a backend's
install is looked for:

```{eval-rst}
.. autodata:: dstf.backends.locate.COMMAND_LINE
```

```{eval-rst}
.. autodata:: dstf.backends.locate.SETTING
```

```{eval-rst}
.. autodata:: dstf.backends.locate.ENVIRONMENT
```

```{eval-rst}
.. autodata:: dstf.backends.locate.SEARCH
```

```{eval-rst}
.. autoclass:: dstf.backends.base.Diagnosis
```

```{eval-rst}
.. autoclass:: dstf.backends.types.TestRunResult
```

```{eval-rst}
.. autoclass:: dstf.backends.types.TestResult
```

```{eval-rst}
.. autoclass:: dstf.backends.types.VariableResult
```

```{eval-rst}
.. autofunction:: dstf.backends.types.resolve_variable_patterns
```

```{eval-rst}
.. autofunction:: dstf.discovery.test_registry.diagnostic_names
```

```{eval-rst}
.. autoclass:: dstf.discovery.test_registry.TestModel
```

### Backends that stay open

```{eval-rst}
.. autoclass:: dstf.backends.persistent.Worker
   :members: __init__, start, is_alive, tool_gone, run_test, reload, process_detail, cancel_running, _track_launch, _evaluate_from_disk, _detach_interface, _shutdown_interface, _quiet, startup_bound, start_within, request_cancel, close, run_test_with_timeout, export_fmu
```

```{eval-rst}
.. autoclass:: dstf.backends.persistent.PersistentBackendBase
   :members: setup_before_workers, make_worker, starting_workers_message, _probe_worker_version, _probe_library_versions
```

```{eval-rst}
.. autoexception:: dstf.backends.persistent.WorkerStartError
```

```{eval-rst}
.. autoexception:: dstf.backends.persistent.LibraryReloadError
```

```{eval-rst}
.. autoclass:: dstf.discovery.library_state.LibraryChanges
```

## Comparison modes

```{eval-rst}
.. autofunction:: dstf.comparison.registry.register_metric
```

```{eval-rst}
.. autoclass:: dstf.comparison.registry.MetricDescriptor
```

```{eval-rst}
.. autoclass:: dstf.comparison.modes.ComparisonMode
   :members: name, compare, is_baseline_free
```

```{eval-rst}
.. autofunction:: dstf.comparison.mode_schema.derive_schema
```

```{eval-rst}
.. autoclass:: dstf.comparison.mode_schema.Schema
```

```{eval-rst}
.. autoclass:: dstf.comparison.mode_schema.FieldSpec
```

```{eval-rst}
.. autoclass:: dstf.comparison.sides.Sides
```

```{eval-rst}
.. autoclass:: dstf.comparison.types.VariableComparison
```

### Groups

```{eval-rst}
.. autofunction:: dstf.comparison.registry.register_combinator
```

```{eval-rst}
.. autoclass:: dstf.comparison.registry.CombinatorDescriptor
```

```{eval-rst}
.. autoclass:: dstf.comparison.metric_tree.Combinator
   :members: combine
```

```{eval-rst}
.. autoclass:: dstf.comparison.metric_tree.MetricResult
```

## Recognizers

A recognizer declared in `testing.json` needs no Python: its keys are on
[](recognizers.md).

```{eval-rst}
.. autofunction:: dstf.discovery.recognizer.register
```

```{eval-rst}
.. autoclass:: dstf.discovery.recognizer.Recognizer
   :members: recognize, applies_to_path
```

```{eval-rst}
.. autoclass:: dstf.discovery.recognizer.RecognizerResult
```

## Running a suite

```{eval-rst}
.. autofunction:: dstf.pipeline.run_pipeline
```

```{eval-rst}
.. autoclass:: dstf.pipeline.RunOutcome
```

Its arguments come from these:

```{eval-rst}
.. autoclass:: dstf.config.Config
```

```{eval-rst}
.. autoexception:: dstf.config.ConfigError
```

```{eval-rst}
.. autofunction:: dstf.discovery.test_registry.discover_tests
```

```{eval-rst}
.. autofunction:: dstf.backends.select_backend
```

```{eval-rst}
.. autoexception:: dstf.backends.BackendUnavailableError
```

```{eval-rst}
.. autoclass:: dstf.storage.reference_store.ReferenceStore
```
