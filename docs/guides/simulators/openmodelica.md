# OpenModelica

## Install

Install OpenModelica from <https://openmodelica.org/> so that `omc` is on
`PATH`, or set the `OPENMODELICAHOME` environment variable to its install
folder, or name `omc` in the OpenModelica block's `executables`.

dstf runs it two ways:

- **workers that stay open** (the default): each worker is one `omc`, driven
  through OMPython, which loads the library once. OMPython comes with the
  `om` extra:

  ```console
  $ uv tool install "dynamic-systems-testing-framework[om]"
  ```

- **`--batch`**: each test runs an `omc` script, and needs only `omc`.

A large library on a slow disk can take minutes to load into a worker. If a
start runs out of time, its message names
{ref}`worker_startup_timeout <key-testing-worker-startup-timeout>`.

Libraries the tests need are listed in `dependencies`; `"Modelica"` loads the
Modelica Standard Library that came with OpenModelica.

Every run records omc's timing statistics and the model's number of states
(from the initialization file omc writes), stored with the baseline: a run
whose number of states differs carries a warning.

## Check

When it is found:

```text
OpenModelica
  executable: /usr/bin/omc
    found by: search: PATH
  OMPython installed: True
  OMPython version: 4.0.1
  import ok: True

OK — OpenModelica is ready.
```

When the block names an `omc` that is not there:

```text
OpenModelica
  executable: not found — the configured executables: /opt/openmodelica/bin/omc does not exist
  OMPython installed: True
  OMPython version: 4.0.1
  import ok: True

FAIL — OpenModelica is not ready. Fix one of:
  1. Install OpenModelica and put `omc` on PATH, or set "executables" in the OpenModelica block of testing.json's "backends"
```

## Settings

The OpenModelica block takes `executables` alone: {ref}`backend-openmodelica`.

<!-- snippet: testing -->
```json
{
  "source_path": "../..",
  "dependencies": ["Modelica"],
  "backends": {
    "OpenModelica": {"executables": ["/usr/bin/omc", "omc"]}
  }
}
```
