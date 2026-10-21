# Changelog

All notable changes to DSTF are written here, newest first. The format follows
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/), and versions
follow [semantic versioning](https://semver.org/). DSTF is in beta: the command
line and the `testing.json` keys may still change between minor versions, and
this file says when they do.

## [Unreleased]

### Changed

- `VariableComparison`'s `rmse`, `signal_range`, `max_abs_error`,
  `max_abs_error_time`, `reference_final` and `actual_final` have defaults
  (`inf`, `0.0`, `inf`, `0.0`, `nan`, `nan`: not measured), and a mode sets
  only those its summary reports. A tube's `nrmse` is its worst violation, as
  a range's is (it was an NRMSE nothing scored by). Its `diagnostics` no
  longer carry what nothing showed: the embedded spectra,
  `detected_reference_peaks_hz`, `peaks_declared`, `max_rel_error`,
  `reason`, `max_time_delta`, `reference_scalar`, `actual_scalar`, and a
  points final check's `tolerance` and `delta`.
- A window that holds no samples scores nothing. A points or event-timing
  criterion whose window holds none of the run's is not scored and FAILS
  saying so (`no actual samples to check (empty trajectory or window)`), as
  are auto-detected events whose baseline holds none there (`no reference
  samples to compare …`): in either case auto-detected events with none on
  the other side used to PASS.
  A dominant-frequency criterion with no peaks declared is not scored either
  over a baseline with no samples (`no reference samples to take a spectrum
  of …`) or fewer than four (`too few reference samples to take a spectrum
  of (fewer than 4)`), where it failed as "No peaks declared".

### Fixed

- A test whose nrmse criterion scored NaN (a NaN in its run) reads `nan` as
  its Worst NRMSE, on the dashboard, its report page and the tool's Suite
  page, and sorts above every number; it read its other criteria's worst, or
  `n/a (1 unscored)`.
- The Worst NRMSE column's "unscored" count is the criteria that were not
  scored. It counted every criterion whose number was not finite, so a tube
  or an nrmse over a NaN sample, and a dominant-frequency criterion with a
  peak not found or none declared, read as unscored though each was scored.
- A points criterion with a lower/upper tolerance pair, whose curve is
  infinite throughout its time box, names that value as the closest one
  (`closest value inf is outside [0.9, 1.2]`) instead of saying no sample
  compares.
- A dominant-frequency peak whose two neighbouring bins have exactly equal
  magnitudes is found as one peak; it was no peak at all, and a declared
  peak at that resonance failed.
- A criterion on a variable the run or the baseline lacks is named by its own
  metric (`x: scalar · against primary`), not as an nrmse one; with no
  metrics tree, one whose baseline lacks the variable now says so
  (`variable 'x' not found in the baseline`), with the hint to rename a
  baseline stored under the expression.
- A dominant-frequency criterion on a run of fewer than four samples reads
  "not scored — signal too short for FFT", not "No peaks declared".
- A `testing.json` dependency written `~/…` is under the home directory; it
  was read as a folder named `~` beside the file.
- A points criterion whose curve jumps to an infinite value inside its time
  box no longer passes as if it had crossed the band there.
- In `dstf author`, the editor of a criterion that was not scored says why
  (`Not scored: variable 'v' not found in the baseline. …`) where it said
  `nrmse has no on-plot handles` or offered handles it did not draw, and its
  hint no longer offers a Ctrl+click that did nothing; its fields still edit
  it, and its window's ⌖ still sets them from the plot where one is drawn. On
  a card that draws no plot (none of its variables is in the result), the
  editor no longer offers a ⌖ that waited for a click on nothing.

### Removed

- `TestModel` no longer has `source_package`, `short_name`, `result_file`,
  `x_raw` or `error_expected`: nothing read them. A plug-in that builds a
  `TestModel` drops those arguments.
- `TestModel.x_reference` and `RecognizerResult.x_raw` / `error_expected` /
  `x_reference` are gone, and the bundled recognizer no longer reads
  `errorExpected` or `x_reference`: nothing read them. A `testing.json`
  recognizer whose `fields` names `x_raw`, `error_expected` or `x_reference`
  now fails to load (`targets unknown field`); drop that field. A field's
  `shape` is `scalar` or `array`: `array_floats`, which only `x_reference`
  could use, is gone, and any other shape, read as `scalar` until now, fails
  to load.
- A criterion's `MetricResult.diagnostics`, what a plug-in `Combinator.combine`
  receives, no longer carries `mode`, `tolerance` or `max_abs_error`; they are
  its `diagnostics["variable"]` (a `VariableComparison`): `mode`,
  `tolerance_used` and `max_abs_error`. Only nrmse, tube and points measure
  `max_abs_error`; range, scalar, event-timing and dominant-frequency leave it
  `inf` (not measured), as does a criterion that was not scored, and their own
  number is the criterion's `nrmse`: a range's or a bounded scalar's worst
  violation, a scalar's deviation from its baseline, the largest event Δt, the
  largest relative peak error. The removed `tolerance` was the tolerance the
  criterion was scored with, as one number where its two sides are equal and
  a `lower`/`upper` dict where they differ; `tolerance_used` is that tolerance
  as a `Sides`, whose `lower` and `upper` are the two sides.

## [1.2.0] - 2026-10-19

### Added

- Accepted baselines record what produced them — backend, simulator and
  library versions, DSTF version, OS, when the run started (`provenance`).
  Existing baselines gain it at their next accept.
- A test entry's own `diagnostic_variables`: names whose summary its
  baseline stores, beside `testing.json`'s.
- `dstf.discovery.test_registry.diagnostic_names` is public API for a
  plug-in backend: the names its `read_result` records as `diagnostics`.

### Changed

- `soft-check add` no longer stores a source's per-variable keys that nothing
  in DSTF reads (`unit`, `description`, …); one warning line names the keys
  it dropped and (up to three of) the variables that carried each.
- CPUtime and EventCounter are variables: any criterion may score them. A
  listed name's curve is stored only when a criterion needs it or the
  entry's `variables` match it; a scalar's final, min or max over the whole
  run is scored from its stored summary.
- A variable's number is written
  `{"variable": "X", "quantity": {"reduce": "final"}}`. `"quantity": "X.final"`
  and `"quantity": "X"` now fail "not found", with a hint naming that
  spelling; a dotted `quantity` names a statistic of the log only.
- Baselines no longer hold `statistics.CPUtime` / `statistics.EventCounter`:
  ignored on read, gone at the next accept.
- FMPy, Julia and Python now record the listed names their results hold.
- On the Test page a listed name's number (a scalar's final, min or max) is
  on the Statistics card, not a plot; in the report it is in the metric tree
  and in no plot's criteria, as a statistic's criterion always was. The add
  form's picker has no Diagnostics section: every name is under Other
  variables, with every metric.
- A listed name's Statistics row is there only while the test lists it,
  whatever a stored baseline still holds. The report's Statistics table
  shows each listed name a side holds. The Test page's Statistics card also
  has a row for a number its criteria score that the table lacks — a listed
  name nothing holds yet, and a listed name's min or max (`X.min`, `X.max`)
  — and the report lists those criteria in its metric tree.
- A report's `comparison_data.json` no longer carries `diagnostic_summaries`,
  which no page showed: a listed name's numbers are its rows in
  `statistics_sections`.

## [1.1.0] - 2026-10-14

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
  test), is refused. A suffix is compared without its case, and a Python
  test runs a `Ramp.PY` as it runs a `Ramp.py` (it failed with `Cannot
  load`).
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

[1.2.0]: https://github.com/ORNL-Modelica/DynamicSystemsTestingFramework/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/ORNL-Modelica/DynamicSystemsTestingFramework/compare/v1.0.1...v1.1.0
[1.0.1]: https://github.com/ORNL-Modelica/DynamicSystemsTestingFramework/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/ORNL-Modelica/DynamicSystemsTestingFramework/releases/tag/v1.0.0
