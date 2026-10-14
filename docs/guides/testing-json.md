# testing.json for your library

`testing.json` is a project's settings: where its library is, which simulator
runs it, where its baselines are kept. Every command reads it through
`--config`. This page sets one up; the
[reference](../reference/testing-json.md) lists every key.

## Where it lives

Keep it in the folder that holds the project's baselines, the reference root,
inside your library's repository so both are versioned with the library:

```text
MyLib/
├── package.mo
└── Resources/
    └── ReferenceResults/
        ├── testing.json
        ├── test_spec.json
        └── Dymola/linux/ref_0001.json ...
```

Every relative path in the file is relative to the file itself. With the
layout above, the reference root is the file's own folder (it is named
`ReferenceResults`) and `test_spec.json` is found beside it, so a Modelica
library needs one key:

<!-- snippet: testing -->
```json
{
  "source_path": "../.."
}
```

Then every command takes the file's path:

```console
$ dstf --config MyLib/Resources/ReferenceResults/testing.json discover
```

## The keys most projects set

- {ref}`source_path <key-testing-source-path>` — the library under test.
- {ref}`source_type <key-testing-source-type>` — for a library that is not
  Modelica: FMUs, a Julia project or Python tests.
- {ref}`backend <key-testing-backend>` and
  {ref}`backends <key-testing-backends>` — which simulator runs the tests, and
  where it is installed (below).
- {ref}`dependencies <key-testing-dependencies>` — libraries loaded before
  yours, such as the Modelica Standard Library.
- {ref}`tolerance <key-testing-tolerance>` — the project's comparison
  tolerance, for a test that sets none.

A project that keeps its files elsewhere sets
{ref}`reference_root <key-testing-reference-root>`,
{ref}`test_spec <key-testing-test-spec>` or
{ref}`work_dir <key-testing-work-dir>`. A key that is not in the reference is
an error that names it, with the key you probably meant, so a typo never
passes silently.

A Modelica project run by Dymola, with OpenModelica as the second choice:

<!-- snippet: testing -->
```json
{
  "source_path": "../..",
  "dependencies": ["Modelica"],
  "tolerance": 0.001,
  "backends": {
    "Dymola": {},
    "OpenModelica": {}
  }
}
```

## How a backend is found

Each entry of `backends` is a backend's name and its settings. An empty block
`{}` means: search for it, on `PATH` and in the simulator's usual install
folders. To pin an install, list where it is under `executables`:

<!-- snippet: testing -->
```json
{
  "source_path": "../..",
  "backends": {
    "Dymola 2025": {
      "executables": ["C:\\Program Files\\Dymola 2025x Refresh 1\\bin64\\Dymola.exe"]
    },
    "Dymola 2026": {
      "executables": ["C:\\Program Files\\Dymola 2026x\\bin64\\Dymola.exe"]
    }
  },
  "backend": "Dymola 2026"
}
```

An entry's name starts with its backend's: `Dymola 2025` is a Dymola. `backend`
picks one entry; without it, the first entry that is found on this machine
runs. A block that pins paths, none of which is there, is not found — no other
install is taken in its place. Each backend's own settings are on
[the reference page](../reference/testing-json.md#backend-settings), and
`--backend` picks an entry for one command.

`dstf check` says what each entry resolves to on this machine:

```console
$ dstf --config testing.json check
```

```text
Backends (testing.json: /home/you/MyLib/Resources/ReferenceResults/testing.json)
  * Dymola        found      /usr/local/bin/dymola  (search: PATH)
    OpenModelica  found      /usr/bin/omc  (search: PATH)
    FMPy          not found  this Python has no fmpy; install the [fmpy] extra
    Julia         found      /home/you/.julia/juliaup/julia-1.11.9+0.x64.linux.gnu/bin/julia  (search: the standard folder /home/you/.julia/juliaup)
    Python        found      /home/you/.local/share/uv/tools/dynamic-systems-testing-framework/bin/python3  (search: the running interpreter)
  * the backend this project runs with
```

Every backend dstf knows is listed, the project's entries first. The list is
followed by the full diagnosis of the one the project runs with, and
`OK — Dymola is ready.` or what to fix. It exits 0 when that backend is
usable. `dstf check OpenModelica` diagnoses another. The
[simulator pages](simulators.md) show what each prints.

## In your editor

`dstf spec schema testing` writes the file's JSON schema, every key with its
description. Point `$schema` at it and an editor checks the file as you type:

```console
$ dstf spec schema testing --output testing.schema.json
```

<!-- snippet: testing -->
```json
{
  "$schema": "./testing.schema.json",
  "source_path": "../.."
}
```

The site publishes the same schema at a fixed address, given on the
[reference page](../reference/testing-json.md#schema).

## Next

- [Add a test](add-a-test.md).
- [Set up your simulator](simulators.md).
