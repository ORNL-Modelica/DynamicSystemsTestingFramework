# Changelog

All notable changes to DSTF are written here, newest first. The format follows
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/), and versions
follow [semantic versioning](https://semver.org/). DSTF is in beta: the command
line and the `testing.json` keys may still change between minor versions, and
this file says when they do.

## [1.1.0] - unreleased

This release changes the command line and `testing.json`. Nothing old is
accepted under another name: a removed command or flag is the parser's error,
and a `testing.json` that holds a 1.0 key that moved is a config error that
names it. [How to convert your testing.json](#how-to-convert-your-testingjson)
lists each one and where it went.

### Changed

- **The command line is a tree.** Commands that belong together are actions
  of one group, and each action offers only the flags it reads. A flag a
  command does not read is an error, wherever it is written:

  <!-- commands: the first column is history -->

  | 1.0 | 1.1 |
  |---|---|
  | `dstf add <model>` | `dstf spec add <model>` |
  | `dstf spec-update <file>` | `dstf spec update <file>` |
  | `dstf export-schema` | `dstf spec schema` (and `dstf spec schema testing` for `testing.json`) |
  | `dstf export` | `dstf baseline export` |
  | `dstf import-baseline <model> <name> <source>` | `dstf soft-check add <model> <name> <source>` |
  | `dstf check-dymola`, `dstf check-openmodelica`, `dstf check-julia` | `dstf check [<backend>]` |
  | `dstf soft-check list` / `remove`, `dstf companion add` / `list` / `freeze` / `remove`, `dstf manifest show` / `dump` / `cleanup` | the same words; each action is now a command with its own help |

  `dstf <command> --help` says what a command does, what it writes and every
  flag it reads, with examples.
- **`backends` in `testing.json` maps a name to a settings block.** Each block
  takes `executables`, the list of paths, and the settings of that
  backend; Dymola's are `show_ide`, `batch_size` and `interface`. An empty
  block `{}` means the backend is searched for. A block whose paths are none
  of them an executable file is not found: no other install is run in its
  place. See [How to convert your testing.json](#how-to-convert-your-testingjson).
- **`testing.json` is strict.** A key nothing reads, or a value of another type
  than its key takes, is a config error that names it (`unknown field
  "tolerence" — did you mean "tolerance"?`, exit 2), from every command that
  reads the file. `description`, `info` and `metadata` are free for notes.
- **Which backend runs when `testing.json` names none:** the first entry of
  `backends` that is found on this machine, else the first entry; with no
  `backends`, the first installed backend that runs the project's
  `source_type`.
- **Dymola's Python interface is the one of the Dymola that was found.** A
  project that names an older Dymola is no longer driven through a newer
  one's interface.
- A command that only reads the project — `discover`, `check`, the lists,
  `baseline export`, `manifest` — no longer writes a default `testing.json`
  where it finds none.
- The report and the dashboard say "criteria" where they said "leaves": a
  test page reads `2 / 3 criteria passed`.

### Added

- `dstf check [<backend>]`: where each backend is on this machine and by
  which rule it was found, the project's own diagnosed in full. It exits 0
  when that backend is usable, and works with no `testing.json`.
- A documentation site, <https://dstf.readthedocs.io/>: a first run, task
  guides, a page per simulator, a plug-in guide, and a reference of every
  command, key and comparison mode generated from the code.
- The JSON schemas of `testing.json` and `test_spec.json` at stable URLs on
  the site, for an editor's `$schema`; `dstf spec schema testing` writes the
  first.
- A worked example of a backend plug-in, `examples/plugins/DecayBackend`.

### Removed

- The flags `--dymola-interface`, `--show-ide` and `--batch-size`: they are the
  Dymola block's `interface`, `show_ide` and `batch_size`.
- The top-level `show_ide` key of `testing.json`, and a `backends` entry
  written as a list of paths.
- The command names `add`, `spec-update`, `export-schema`, `export`,
  `import-baseline`, `check-dymola`, `check-openmodelica` and `check-julia`.
- Abbreviated flags: `--rep` is no longer taken for `--report`.

### Fixed

- `run --parallel` on FMUs failed every test with an XML schema error; the
  tests now run side by side.
- `--backend-path` naming a program that is not there ran the one on `PATH`
  instead; it is now "not found", and so is a configured path that is a
  directory or not executable — `dstf check` and `run` agree.
- A Modelica library beside a directory above the working directory was taken
  for the project's library.
- `--output` naming a directory, a file in a directory that does not exist,
  or a place this user may not write, ended in a traceback after the work was
  done; it is refused before. `manifest dump` into a work dir that cannot be
  written says so in one line.
- A negative base tolerance was accepted and failed every `nrmse` test; it is
  refused.
- `run --batch` on FMPy or Python was silently ignored; it is a config error.
- `companion freeze` of a companion that does not exist exited 0; it exits 1.
- `spec add` of a model that is no class of a Modelica project's library,
  of one of its packages, of the library itself, of a function, a record or
  a connector, or of a partial model wrote an entry no run could simulate,
  and exited 0; it says what the name is (`… is a function, not a model`),
  writes nothing and exits 1.
- `spec add` on a Python, Julia or FMU project wrote an entry that named no
  file, and every run failed it with a Python traceback. `--source FILE`
  names the file the test runs, and is required there; a directory, or a
  file of another suffix than the backend runs (a `.csv` for a Python
  test), is refused.
- A `test_spec.json` entry of a Python or Julia test with no `source` ran the
  library's folder (`Cannot load <library dir>`) instead of the file named
  after its model, `<source_path>/<model>.py` (`.jl`); it runs that file, and
  where there is none, its failure names the path it looked for and says to
  add a `source` or put the file there. An
  FMPy entry with no `source` handed fmpy the folder as its FMU; it fails in
  one sentence that names the entry.
- A hand-written `metrics` tree that scored a soft check outside `warn`
  failed its test on `run` and `compare`, where a soft check may only warn.
  A tree that is wrong in its file — a criterion outside `warn` against
  anything but the primary; no criterion against the primary outside
  `warn`; a `weighted` that sums scores of opposite directions — is now
  refused where it is scored, as `dstf spec update` refused it where it was
  written: the test fails with each rule it breaks, where in the tree
  (`metrics.children[1]: …`), none of its criteria is scored, and an accept
  skips it with that sentence instead of storing its result. A criterion under `warn` against a soft check that is not
  stored for this backend and OS is not scored and fails no test, as
  before; it says so, `no soft check 'experiment' is stored for
  FMPy/macos`. Where such a tree is written, `dstf spec update` and the
  authoring tool refused every edit of that test — and, before its first
  accept, the tree itself; they write it, and say `'experiment' is not
  stored for FMPy/macos: it is not scored here`. A criterion that names a
  companion of this backend and OS is still refused there; where it is
  scored, under `warn`, it says `'rig' is a companion here: it is only
  drawn, never scored` instead of that no soft check of its name is stored.
- The dashboard's copyable commands named `testing.json` by a relative path
  and left out the run's other flags: after `run --reference-root ALT`, its
  `compare --accept` overwrote a baseline in `testing.json`'s own reference
  root and left ALT alone; after `run --backend-path WRAPPER`, its `run`
  simulated with the default interpreter; after `run --tolerance 0.5`, its
  `compare` scored at `testing.json`'s tolerance. A command now carries the
  places the run was given, the default work dir, and `--backend-path`,
  `--tolerance`, `--timeout` and `--default-points` where that command reads
  them, by absolute paths, so it does what the run did from any directory;
  `author` is offered only when it reads them all. A `compare` that renders
  the page after the run keeps the run's settings for what it does not read
  itself: its `run` still carries the run's `--timeout`.
- The default `testing.json` a command writes where it finds none did not
  record the library: `dstf --config <it>` from another directory found no
  project. It now records `source_path`, and the reference root and spec
  where they are not its defaults.
- `dstf author`'s Queue, refused when the backend's workers cannot start,
  told the user to re-run with `--batch`, a flag the tool does not have; it
  names only what can be done there.
- `manifest cleanup --orphans` with a `--test-spec` or `--work-dir` that is
  not there said `No orphan manifest entries.`; it is a config error naming
  the path.
- A project that names no backend ran the next entry of `backends` without a
  word when an earlier one was not found here; `run` and `check` now say which
  entry was passed over and why.
- A `--source-path` (or `source_path`) that is not there was said as an
  OSError run into the hint after it; it is one sentence naming the path.
- A command that writes a default `testing.json` where it finds none wrote it
  even when it then failed its own checks (`companion freeze` of a companion
  that is not there); the file is written only once the command is past them.

### How to convert your testing.json

A `backends` entry was a list of paths. It is now a block, with the list
under `executables`, and Dymola's settings that were flags or top-level keys
move into Dymola's block. Before (1.0):

<!-- snippet: testing-refused -->
```json
{
  "backend": "Dymola",
  "backends": {
    "Dymola": ["C:\\Program Files\\Dymola 2026x\\bin64\\Dymola.exe"],
    "OpenModelica": []
  },
  "show_ide": false
}
```

After (1.1):

<!-- snippet: testing -->
```json
{
  "backend": "Dymola",
  "backends": {
    "Dymola": {
      "executables": ["C:\\Program Files\\Dymola 2026x\\bin64\\Dymola.exe"],
      "show_ide": false
    },
    "OpenModelica": {}
  }
}
```

Each 1.0 key and flag that moved, and where it is in 1.1:

| 1.0 | 1.1 |
|---|---|
| a `backends` entry as a list of paths | that entry's `executables` |
| the top-level key `show_ide` | `backends.Dymola.show_ide` |
| the flag `--show-ide` | `backends.Dymola.show_ide` |
| the flag `--batch-size N` | `backends.Dymola.batch_size` |
| the flag `--dymola-interface PATH` | `backends.Dymola.interface` |

Every other key 1.0 read is read as it was. A `backends` entry still written
as a list is a config error that names every such entry at once, each with
the block to write; a top-level `show_ide` is the error any key nothing
reads is (`unknown field "show_ide"`), and a removed flag the parser's.

## [1.0.1] - 2026-10-13

The first release on PyPI, as `dynamic-systems-testing-framework`; the
command is `dstf`. The code is 1.0.0's.

### Added

- What the package index shows: the README as the project page, the licence
  by its SPDX name, links to the source and the issues.

## [1.0.0] - 2026-10-13

The first tagged version. It was not published to PyPI.

[1.1.0]: https://github.com/ORNL-Modelica/DynamicSystemsTestingFramework/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/ORNL-Modelica/DynamicSystemsTestingFramework/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/ORNL-Modelica/DynamicSystemsTestingFramework/releases/tag/v1.0.0
