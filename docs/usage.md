# Usage Guide

## Setup

### Configuration file (`testing.json`)

Create a `testing.json` in your reference results directory. This is the single entry point for the tool — it tells it where the library is, which backend to use, and how to run tests.

```json
{
  "source_path": "../../MyLibrary/MyLib",
  "backend": "Dymola",
  "backends": {
    "Dymola": {
      "executables": [
        "C:\\Program Files\\Dymola 2026x\\bin64\\Dymola.exe",
        "C:\\Program Files\\Dymola 2025x Refresh 1\\bin64\\Dymola.exe"
      ]
    },
    "Dymola 2025": {
      "executables": ["C:\\Program Files\\Dymola 2025x Refresh 1\\bin64\\Dymola.exe"]
    },
    "Dymola 2026": {
      "executables": ["C:\\Program Files\\Dymola 2026x\\bin64\\Dymola.exe"],
      "show_ide": false
    }
  },
  "dependencies": [],
  "test_spec": "test_spec.json"
}
```

**Fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `source_path` | No | Path to library source location (Modelica package.mo directory, FMU directory, etc.). Relative to `testing.json` location. Can be provided via `--source-path` instead. |
| `source_type` | No | `"modelica"` (the default: `source_path` holds a `package.mo` and its `.mo` files are scanned for tests), or `"fmu"` / `"julia"` / `"python"`, whose tests come from `test_spec.json` alone. |
| `library_name` | No | The library's name for a non-Modelica source (default: the name of the directory holding `testing.json`); a Modelica library's is read from its `package.mo`. |
| `backend` | No | Backend name. Use named entries like `"Dymola 2025"` to target specific versions (an entry is the backend its name starts with). A name that is no entry of `backends` and starts with no backend's name is a config error that lists both. Omitted: the first `backends` entry that is found on this machine, else the first entry; with no `backends`, the first installed backend that runs this `source_type` (for a Modelica library: Dymola, then OpenModelica). |
| `backends` | No | Map of backend names to a settings block each: `{"executables": [...]}` plus that backend's own settings (see "Backend settings" below). `executables` lists candidate paths or program names (the first that is a file that can be executed wins): an entry with a `/` or a `\` in it is a path — a relative one is relative to `testing.json`, and `~` is the home directory — and any other is a program name, looked up on `PATH`; an empty string is a type error. An empty block `{}` — or no block — means the backend is searched for: `PATH`, then its standard install folders. A block whose `executables` holds a path, none of which is such a file — one that does not exist, a directory, a file that is not executable — is NOT found: no other install is taken in its place, and `check` and `run` say which entry and why. |
| `dependencies` | No | Paths to dependency libraries loaded before simulation, relative to `testing.json` location — a library's directory, its `package.mo`, or a one-file library `Name.mo`: each is opened by its file (`<dir>/package.mo`, or the `.mo` file given) on Dymola and OpenModelica alike — or a bare library name (`"Modelica"`), which OpenModelica loads by name. |
| `backend_setup` | No | Modelica commands run after library loading. For user-specific settings only — `OutputCPUtime` and translation log capture are handled automatically by the Dymola backend. |
| `test_spec` | No | Path to external test definitions file. Relative to `testing.json` location. When unset, `<reference_root>/test_spec.json` is used if it exists — the file `spec add` writes — and every command that discovers tests reads it. |
| `recognizers` | No | Declarative recognizers for your library's own test convention, added to the bundled one (see "Test definitions"). |
| `disable_bundled` | No | Names of bundled recognizers to turn off, e.g. `["modelica:bundled-unit-tests"]`. |
| `diagnostic_variables` | No | Variables auto-captured but not compared (default: `["CPUtime", "EventCounter"]`). |
| `max_embedded_samples` | No | The most samples a per-test report page embeds per trace (default: `1000`); longer ones are decimated, and the page's banner says which. |
| `tolerance` | No | The project's comparison tolerance (default: `1e-4`): what a test with no `comparison.tolerance` of its own, in its spec entry or its baseline, is scored with. One finite number — anything else is a config error (see "Tolerances with two sides"). `--tolerance` overrides it. |
| `baseline_points` | No | The points a stored baseline keeps of its run: `"all"` (the default) — every point the simulator wrote — or a whole number of 2 or more, which thins a longer run to at most that many. Anything else is a config error. A `test_spec.json` entry's own `baseline_points` overrides it for that test. A baseline is what a run is compared against, point by point, so this is also the resolution at which a regression can be seen (see "The points a baseline stores"). There is no flag for it. |
| `worker_startup_timeout` | No | Seconds a persistent worker (Dymola, OpenModelica, Julia) may take to start before the run fails with the backend's message. One positive number; default 600 on every backend. The bound is for a tool that hangs — a tool that died fails the start at once — so raise it only for a library whose load takes longer (a large library is minutes of `loadFile` in OpenModelica); the message of a start that ran out names this key and the step the tool was in. |
| `reference_root` | No | Override where references are stored. Defaults to the directory containing `testing.json` if it's named `ReferenceResults`, otherwise `<repo>/Resources/ReferenceResults`. |
| `work_dir` | No | Where runs write their output, relative to `testing.json` location (default: `testing_output/<library>/<backend>/<os>/` under the current directory). `--work-dir` overrides it. |
| `os` | No | The OS partition of the references (default: detected — `windows`, `linux` or `macos`). |
| `theme` | No | The bundled theme the pages are drawn in: `"default"` (the default) or `"dark"`. The dashboard and the per-test report pages are generated in it — set it and re-run or `compare` to regenerate them — and `dstf author` opens in it. A name only: any other value, a path included, is a config error that lists the two. There is no flag for it. |

All relative paths resolve from where `testing.json` is located.

**Backend settings.** Every block of `backends` takes `executables`; the
rest are the backend's own:

| Backend | Setting | Description |
|---------|---------|-------------|
| Dymola | `show_ide` | `true` shows the Dymola GUI instead of running headless (default: `false`). |
| Dymola | `batch_size` | With `run --batch`: tests per Dymola session (default: every test of a worker in one session). Small values (3-5) give better load balancing and crash isolation but reload the library more often. |
| Dymola | `interface` | Dymola's Python interface: the archive (`dymola-*.whl` / `dymola.egg`) or its directory; a relative path is relative to `testing.json`, and `~` is the home directory. Default: the `DYMOLA_INTERFACE_PATH` environment variable, else `Modelica/Library/python_interface` of the Dymola that `executables` found, else the newest Dymola's in the standard install folders. |

OpenModelica, Julia and Python take `executables` alone; FMPy, a Python
library, takes no setting. `dstf check` prints what each backend's block
resolves to on this machine, and by which rule.

**A key nothing reads is an error that names it**, here as in
`test_spec.json`: `Config error: <file>: unknown field "tolerence" — did you
mean "tolerance"?` (exit 2) from every command that reads the file, and
`backends.Dymola: unknown field …` for a key of a block. A value of another
type than its key takes is the same error (`max_embedded_samples: must be a
whole number, got "abc"`). `description` / `info` / `metadata` are for notes,
and `$schema` may point an editor at `dstf spec schema testing`'s
output, which lists every key with its type, default and description.

So the former spellings `simulator`, `simulators` and `simulator_setup` are
refused, as is a `backends` entry written as a list of paths (`backends.Dymola
must be an object — {"executables": [...]} (was a list of paths)`) and the
top-level `show_ide`. There is no alias and no migration path: an old key
never works again, it just says so.

### Test definitions

Tests are discovered from three sources (can be combined; merged by model_id, last writer wins per field):

**1. Bundled in-model recognizer** — finds any class with the `ModelicaTestingLib.Components.UnitTests` component plus the standard `experiment(...)` annotation. Scans `.mo` files automatically, no setup needed.

**2. User-provided recognizers in `testing.json`** (Phase 5 / PTA) — declarative JSON; finds tests via your library's own convention without forcing adoption of `UnitTests`. Example:

```json
{
  "source_path": "../../",
  "backend": "Dymola",
  "recognizers": [
    {
      "name": "mylib:icons-example-as-simulate-only",
      "applies_to": ["modelica"],
      "match": {
        "type": "extends",
        "class_pattern": "*Icons.Example"
      },
      "fields": {
        "simulate_only": {"from": "constant", "value": true},
        "stop_time": {"from": "experiment-annotation", "name": "StopTime"}
      }
    }
  ],
  "disable_bundled": []
}
```

Modelica match types: `component-instantiation` (`component_name`), `extends` (`class_pattern` glob), `class-name-glob`, and `all-of` / `any-of` over those. Field sources: `parameter` (from matched component), `constant` (literal value), `experiment-annotation` (from standard `experiment(...)` block), `annotation` (from another annotation). The schema is at the top of `src/dstf/discovery/json_recognizer.py`. Per-field merge with last-writer-wins lets a user recognizer override individual fields without disabling bundled. To turn the bundled recognizer off entirely (e.g. when a dependency lib's example tests would otherwise be discovered), add its name to `disable_bundled`: `["modelica:bundled-unit-tests"]`. See `examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json` for a working demo.

**3. External `test_spec.json`** — for per-test overrides, MetricTrees, or variable specifications independent of any in-model annotation:

```json
{
  "tests": [
    {
      "model": "MyLib.Examples.SimpleTest",
      "variables": ["pipe.T[1]", "tank.level"],
      "simulation": {
        "stop_time": 100,
        "tolerance": 1e-4,
        "method": "Dassl",
        "number_of_intervals": 500,
        "output_interval": null,
        "timeout": 60
      },
      "comparison": {
        "tolerance": 0.01,
        "variable_overrides": {
          "pipe.T[1]": {"tolerance": 0.1}
        }
      }
    },
    {
      "model": "MyLib.Examples.MinimalTest",
      "variables": ["*"]
    }
  ]
}
```

The first entry shows every field `simulation` accepts; the second shows the minimum (everything else falls through to the annotation, then to backend defaults). All `simulation.*` fields are optional.

Variable patterns support `*` and `?` wildcards. `["*"]` tracks all non-parameter variables. `[]` (empty list) simulates without tracking variables.

**Per-test fields inside `simulation` (all optional):**

| Field | Type | Description |
|---|---|---|
| `stop_time` | float | Simulation end time. Overrides the model's `experiment` annotation. |
| `tolerance` | float | Solver tolerance. |
| `method` | string | Solver method name (e.g. `"Dassl"`, `"Esdirk45a"`). |
| `number_of_intervals` | int | Output sample count. |
| `output_interval` | float | Output interval (alternative to `number_of_intervals`). |
| `timeout` | int | Per-test timeout in seconds. Overrides the global `--timeout` flag. Honored by every backend: Dymola / OpenModelica / Julia via the persistent-worker watchdog; OpenModelica and Julia also enforce it per test on their `--batch` paths, while batch Dymola sums the per-test values into a single subprocess budget, so there the limit is per batch, Python via the JSON-driver subprocess timeout, FMPy via `concurrent.futures` (its worker thread is left to finish in the background, since FMPy runs in-process). |

**Non-Modelica backends**: add a `"source": "path/to/Model.fmu"` field (path relative to the spec file) to point at the simulation source — a prebuilt `.fmu` for FMPy, a `.jl` for Julia, a `.py` for Python.

**MetricTree**: add a top-level `"metrics"` block to author an explicit pass/fail tree — see `docs/extensibility.md` §6 for the schema.

**Baseline points**: add a top-level `"baseline_points"` to an entry — `"all"` or a whole number of 2 or more — to say how many points THIS test's stored baseline keeps, over `testing.json`'s `baseline_points` (see "The points a baseline stores").

#### A key nothing reads is an error

`test_spec.json` is strict about its keys: one that nothing reads stops the command and is named — with the key that was meant when the name is a near miss of exactly one (a letter or two off, another case, the start of a one-word name: `tol` for `tolerance`), otherwise with the fields there are to choose from. A hint is never a guess: `start_time` is not offered `stop_time`.

```
Config error: …/test_spec.json: tests[MyLib.Examples.SimpleTest].metrics.children[1]: range · pipe.T[1]: unknown field "max_val" — did you mean "max_value"?
```

It used to be kept and ignored, and that leaf scored as a range with no upper bound. The check covers:

- the file's own keys: `tests`, and `$schema` for an editor (`{"Tests": [...]}` used to be a spec of no tests);
- a test entry's own keys, and those of its `simulation` and `comparison` blocks;
- a `comparison.variable_overrides` entry: `mode` and that mode's fields (with no `mode`, the default metric's — or `points`'s, which `--default-points` scores it with);
- a `metrics` leaf: `metric`, `variable`, `against`, `window`, and its metric's fields; a combinator: `combinator`, `children` and what its kind takes (`k` on a `k-of-n`; `weights`, `threshold`, `direction` on a `weighted`); a `window`: `start`, `end`;
- each row of a metric's table — `points`, `peaks`, `events`, `tube_points`;
- the keys inside a tolerance pair (`lower`, `upper`) and inside a `scalar`'s `quantity` object (`reduce`);
- `plots` (see "Plot layout").

`tolerance` is a key of every leaf and override. `quantity` is one too: on a metric other than `scalar` it is the one param that is taken and not used. For your own notes there are `description`, `info` and `metadata` (an object of any shape), on an entry and on its `comparison` block; nothing reads them.

`run`, `compare`, `discover` and `dstf author` report it as a `Config error` and exit 2, as for a malformed `metrics` tree — `dstf author` before it serves anything, like any error in `testing.json`. A spec edited by hand into one while the tool is open is said in the page, with the same sentence, when the project is opened again. `spec update` refuses a patch that would write such a key (`Patch failed: …`, exit 1, the file untouched), and the tool refuses to stage one, saying the same sentence. A criterion that could never be scored — a `range` with neither bound, a `range` or `scalar` bound that is no finite number (`"abc"`, `true`, `NaN`), a `scalar` with no `quantity` — is refused where the file is written, and only by the write that makes it: `spec update` refuses the patch (`Patch failed: … tests[M].metrics.children[3]: range · pipe.T[1]: needs at least one of min_value / max_value`), and the tool stages it — its row says why it is not scored — and refuses the Save with one sentence until it is filled or removed. One the file already holds blocks no other edit of its entry, the edit that fills it and the edit that removes it included. Under a `metrics` tree the flat `variable_overrides` score nothing, and are not checked for this. `spec schema` publishes the same rules: its objects are closed (`additionalProperties: false`) wherever the parser is strict.

A block that is not an object where one is read — `"simulation": "stop_time=5"`, `"comparison": null`, `"variable_overrides": [1]`, an override written `"x": "tube"`, `"metrics": null`, a leaf's `"window": null` — is the same error, naming the block and what it holds; leave a block out to not set it. A window's `start` and `end` are numbers (`"start": null` is refused too: leave it out for a window open at that end).

So is an entry that cannot be a test, and a value that is not what is read there: an entry of `tests` that is no object (`tests[3]: must be an object, got "MyLib.A"`) or has no `model`; `variables` that is not a list of strings (`"variables": "x"`, `[1]`); a `source` that is no string; and a `simulation` value of another type than its field's — `"stop_time": "abc"`, a number written as text (`"100"`), `true`, a `number_of_intervals` of `2.5`: `tests[MyLib.A].simulation.stop_time: must be a number, got "abc"`. (`null` there still means "not set".) Each used to be a warning in the log and a test skipped, or run on defaults.

And so is a spec file that is no spec at all — one line, exit 2, from every command that reads it (`discover`, `run`, `compare`, `spec update`, `spec add`, `dstf author`), and nothing is run or written:

```
Config error: …/test_spec.json: not JSON (Expecting value: line 1 column 12 (char 11))
Config error: …/test_spec.json: the file is empty
Config error: …/test_spec.json: not UTF-8 text ('utf-8' codec can't decode byte 0xff in position 0: invalid start byte)
```

A file saved with a UTF-8 byte-order mark is read, and a key written twice keeps its last value. `testing.json` is decoded by the same decoder, with the same three sentences under its own path — a number of thousands of digits, or brackets nested by the ten thousand, is `not JSON (…)` in either file. A project whose spec file does not exist, or holds `{}` or `{"tests": []}`, has no spec tests, which is no error.

The `comparison` section a baseline stores in its reference JSON goes through the same check: its `variable_overrides` are merged under the spec's, so a stored override with a key nothing reads stops a command that would compare against it — `Config error: <reference file>: comparison.variable_overrides[x]: …`, exit 2, from `run` before it simulates anything and from `compare` — while `dstf author` opens and shows that one test FAIL, `Not scored:` with the same sentence.

```
Config error: …/Dymola/linux/ref_0007.json: comparison.variable_overrides[pipe.T[1]]: range · pipe.T[1]: unknown field "max_val" — did you mean "max_value"?
```

#### Two-layer contract: annotation vs `test_spec.json`

Modelica simulation parameters resolve from two layers, in order:

1. **`experiment(...)` annotation** on the model — the source-of-truth defaults the model author encoded. Recognized fields: `StopTime`, `Tolerance`, `__Dymola_Algorithm` (→ `method`), `NumberOfIntervals` (or `__Dymola_NumberOfIntervals`), `Interval` (→ `output_interval`).
2. **`simulation.*` block in `test_spec.json`** — per-test override. Any field set here wins over the annotation.

Resolution rule: **user omits the field → annotation if present, else simulator default. User provides the field → it is used.** No third layer; no per-field "force annotation" toggle (delete the override to defer to the annotation).

The rule is the same for a test declared only in `test_spec.json` (`dstf spec add MyLib.Examples.Rod`): its model is looked up in the library by name — its own `.mo` file, or the `package.mo` or parent-class file that holds it — and what the entry does not set comes from that model's `experiment(...)`. A short class (`model A = B(...) annotation(experiment(StopTime=13));`) is looked up like any other and gives its OWN annotation — not the one of the class it is made from, which no test inherits here. A model of another library (a dependency) is not looked up. When nothing gives a stop time, the test runs to the framework's default of 1 s, and `run` says so before it starts, with the reason that is true of that test: `MyLib.Examples.Rod: neither test_spec.json nor the model's experiment annotation gives a stop time; it runs to the default, 1 s.` for a model that was found; `Modelica.Blocks.Examples.PID_Controller: not found under source_path, so its experiment annotation was not read; it runs to the default, 1 s.` for one that was not (give it a `simulation.stop_time`); and, for a source type with no annotation layer (FMU, Julia, Python), `…: test_spec.json gives no stop time; it runs to the default, 1 s.`

Use case for the two layers: a model author can write `experiment(StopTime=100)` so the model demos cleanly in Dymola/OMEdit, then set `simulation.stop_time = 10` in `test_spec.json` so CI runs fast — no need to edit the source.

**Known gap**: `StartTime` from the annotation is not honored — every backend assumes `t=0`. Authoring `StartTime=5.0` in the annotation will be silently ignored. If you need a non-zero start, file an issue with the use case.

#### Plot layout (`plots`)

A test's plots are **derived from its criteria** (D140): one plot per scored
variable or array family, in the order the metric tree first names it (a
depth-first walk; a family sits where the tree first names any member). A
test with no tree — nothing scored, no `metrics` — draws one plot per tracked
variable or array: the test's `UnitTests` elements first, then the spec's
`variables` in the order they wrote them (a glob's matches in the result
file's order). A run that failed has no plots. The
report and `dstf author` draw the same plots. A criterion on a variable IS
its plot: a plot goes when its last criterion goes.

The optional `plots` key holds only what a person adds to that — its
**extras**. Scoring ignores it.

```json
{
  "model": "MyLib.Examples.PipeTest",
  "variables": ["pipe.m_flow", "unitTests.x[6]"],
  "plots": [
    {"var": "unitTests.x[6]", "context": ["unitTests.x[10]"]},
    {"var": "pump.N"}
  ]
}
```

- An entry whose `var` is scored (a leaf of the tree names it, or a member of
  its family) adds its `context` to that variable's plot: **context series**
  draw grey and thinner on the plot's own y axis, not scored, named
  `<label> · context` in the legend.
- An entry whose `var` is not scored is a **watch plot**: a plot of that
  variable with no criteria yet, after the derived plots, in the order the
  entries are written. A watch plot whose variable later gets a criterion
  becomes that variable's derived plot, keeping its context; a derived plot
  whose last criterion goes stays as a watch plot while its entry is there.
- `var` names one variable of the result file — the name the file gives it,
  so a variable of the `UnitTests` component is `unitTests.x[i]` and not the
  text the model wrote for it (see "A variable's name, and its expression"
  below). `context` is a list of such names: optional, no duplicates, not
  the entry's own `var`. A pattern (`*`, `?`) is refused anywhere; square
  brackets are part of a name. Two entries with the same `var`, unknown keys
  and `null` are refused; absent or `[]` means no extras.
- **Families.** An element of the test's `UnitTests` component is always a
  plot of its own, whatever its expression reads — `pipe.T[3]` and
  `cat(1, pipe2.mediums.T, pipe2.mediums.p)[3]` alike: it is a test
  quantity of its own. Any other array element folds by its RESULT-FILE NAME
  into one family plot — `pipe.T[1]` .. `pipe.T[11]` draw as one hue ramped
  light to dark by index, one legend entry `pipe.T[1:11]` — on its last
  single-integer index (`x[2,3]` is no array position). There is no scale
  check.
- The overlays and the error select belong to a plot whose subject is one
  variable; context does not count.
- A name the result does not hold draws nothing and the plot says "Not in
  this result: …". Which untracked variables a result holds depends on the
  backend: Dymola, Julia and Python result files hold every variable;
  OpenModelica's and FMPy's hold only the tracked ones.

The key's shape before D140 (`{"kind": "time", "series": [...]}`, a whole
layout) is refused; no spec used it, so nothing migrates.

**Components.UnitTests** (the in-source `n=N, x={...}` block) is a **discovery marker** carrying the variable list only. It deliberately does not carry simulation parameters — those live in the annotation (model-author-facing, editor-honored) or in `test_spec.json` (test-author-facing, override-only). Keeping the third layer out is what makes the contract teachable.

#### A variable's name, and its expression

A tracked variable is named by the **result file**, and everything that
addresses one — a `comparison.variable_overrides` key, a `metrics` leaf's
`variable`, a `plots` entry's `var` and `context`, a baseline entry, the element ids on the
report page — uses that name.

For a variable of the `UnitTests` component the result file names it by its
POSITION in the component's array: `unitTests.x[1]`, `unitTests.x[2]`, … (the
instance name is whatever the model called the component). The text the model
wrote inside `x={...}` — `pipe.T[1]`, `cat(1, eta, lambda)[2]` — is the
variable's **expression**. It is a label:

- stored beside the name in the reference JSON (`{"index", "name",
  "expression", "values"}`, always written, `""` included);
- what the console, JUnit, the report page and `dstf author` READ for that
  variable. ONE rule decides the text, and its scope is the TEST — every
  name the test's page can show: the result's tracked variables, the scored
  comparisons' variables and every variable of the test's plots, context
  series and watch plots included. A variable reads as its expression, unless another name in
  that scope carries the same expression — `x={a.Q, a.Q}` is legal — and
  then the name rides with it: `a.Q (unitTests.x[1])`. A variable whose
  label IS its name is never spelled twice, so a context variable
  genuinely named `z` keeps its plain `z` beside a tracked one whose
  EXPRESSION is `z`.
  The two surfaces that show the name beside the title anyway — the
  report's variable heading and the tool's card title — read the plain
  expression there, because the muted name already says which variable it
  is. Every other human-facing surface reads the disambiguated text:
  criteria rows and tree leaves, status pills, the focus bar, the "Add a
  criterion for …" captions, the add form's options, the plot notes,
  legend entries and hovers, and the decimation banner.
  The console, JUnit and the interactive review are outside that rule and
  print `expression (name)` whenever the two differ: a line of CI output is
  read with no page around it, so there is no scope to disambiguate within;
- soft-checked on every compare: if the baseline recorded one expression and
  this run measures another, the test gains a structural warning naming both
  — never a failure. That is what makes the positional name safe.

It is a key of nothing. The one exception is overlay matching, and even there
the expression is only the fallback: a companion records which of ITS
variables draw on which of the test's, in its own metadata (see
[Companions](#companions) below and `docs/decisions.md` D132). A companion
with no mapping is matched by name, then by expression, because another tool's
result file cannot be expected to spell a positional name. A soft-check or
sibling-backend overlay is DSTF's own data under the test's own names, and
is matched by name alone.

A key or a stored name written as the expression text names nothing; it has
to be the result-file name. Scoring says so in two places, and names the
name to write:

- a `variable_overrides` key that is a variable's expression is ignored, and
  the run logs one line per test naming the key, the file that holds it
  (`test_spec.json` or the reference JSON's own `comparison` section) and the
  variable — or variables, for `x={a, a}` — whose expression it is. Write that
  name as the key.
- a baseline entry stored under the expression reads as a missing baseline
  for that variable. The log line — and, for a test with a `metrics` tree,
  the criterion's own reason — says the baseline stores it under its
  expression and gives the result-file name the entry's `name` must be.
  Rename the entry, or accept the test again (accept it again where the test
  tracks the same quantity twice — a `variables` pattern and a UnitTests
  element — and the baseline holds it once).

A `metrics` leaf's `variable`, or a `plots` entry, written as the expression
gets no such hint: the leaf reads `variable '…' not found in the result`.
With a `metrics` tree the flat `variable_overrides` score nothing, and their
keys are not looked at.

### Scalar leaves — comparing one number

The `scalar` metric scores a single number instead of a trajectory. Its required `quantity` field says which number, in one of two forms.

**Series reduction** — reduce the leaf's tracked `variable` (after the leaf window, if any) on both sides. Reductions: `max`, `min`, `final`, `integral` (trapezoid over the windowed series), and two means — `mean` is the **time-weighted** mean (that integral divided by the time span), so a finer output grid or extra samples around an event can't shift the score of an identical trajectory, while `sample_mean` is the plain arithmetic mean of the samples, for parity with tools that report that number. A single sample degrades `mean` to the sample mean.

```json
{
  "metric": "scalar",
  "variable": "tank.level",
  "quantity": {"reduce": "max"},
  "tolerance": 0.02,
  "tolerance_mode": "rel"
}
```

**Stats path** — a dotted string reads a stored scalar: first a lookup into the run's `statistics`, then the last segment as a diagnostics summary field (`final` / `min` / `max`). Statistics win on a name collision (`EventCounter` appears in both). `variable` is optional here — the path is the label unless you set one.

```json
{"metric": "scalar", "quantity": "translation.scalar_unknowns", "tolerance": 0}
{"metric": "scalar", "variable": "solver cost", "quantity": "CPUtime.final", "tolerance": 0.5}
```

`tolerance` is one number, or a pair `{"lower": a, "upper": b}` that says each side on its own (see "Tolerances with two sides", below). With `d = actual - reference`, `tolerance_mode` `"abs"` (the default) passes when `-lower <= d <= upper` — for a number, `|actual - reference| <= tolerance` — and `"rel"` takes each side as that fraction of `|reference|`, falling back to the absolute test when the reference is 0. Declaring `min_value` and/or `max_value` switches the leaf to a **baseline-free bounds check** — the scalar must lie inside the inclusive bounds, and no reference is consulted:

```json
{"metric": "scalar", "variable": "pump.W", "quantity": {"reduce": "mean"}, "max_value": 5000}
```

A tree scores only what its leaves name. Every other tracked variable — one the
`variables` list or a pattern in it still matches — is stored, plotted and
never judged, and the comparison carries a structural warning naming it on
the console and the report page: `Tracked but not scored by any criterion:
tank.level`.
The flat path (no `metrics` block) has no such gap: it scores every tracked
variable. So a pattern such as `"variables": ["tank.*"]` keeps tracking new
matches after a tree exists, and the warning is how you learn one needs a leaf.

A window on a stats-path leaf is ignored — a stored scalar is not a time series. A stats-path leaf needs a `metrics` tree: in a flat `comparison.variable_overrides` entry the leaf only has the variable's own trajectory, so a string `quantity` there is a hard failure rather than a silent reduction of the wrong signal (a `{"reduce": ...}` override is fine).

### Tolerances with two sides — a number or a lower/upper pair

A criterion's tolerance is one number, or the pair `{"lower": a, "upper": b}`,
which says each side on its own. A number is both sides: `0.02` and
`{"lower": 0.02, "upper": 0.02}` score the same, and a spec that holds only
numbers scores exactly as it did before a pair existed.

**What the sides mean.** With `d = actual − target`, a check passes when
`−lower ≤ d ≤ upper`: `lower` is how far under its target the value may be,
`upper` how far over. In a relative mode each side is that fraction of
`|target|`. In time, `lower` is how much EARLIER and `upper` how much LATER.

**Which fields take a pair.**

| Metric | Field | Its two sides |
|---|---|---|
| `points` | the criterion's `tolerance`, and a `points` row's own `tolerance` | under / over the checkpoint's target (its `value`, else the baseline at its time) — signal units, or fractions of the target's magnitude in a `rel` row |
| `points` | a `points` row's `time_tolerance` | before / after the checkpoint's time |
| `event-timing` | `time_tolerance`, and an `events` row's own `tolerance` | early / late |
| `dominant-frequency` | a `peaks` row's `tolerance` | under / over the declared frequency — fractions of it in `rel` (the default), Hz in `abs` |
| `scalar` | `tolerance` | under / over the baseline's scalar — fractions of its magnitude in `rel` |
| `tube` | `tube_abs` (`band`) and `tube_rel` (`rel`) | under / over the reference |

The same keys take a pair in a `comparison.variable_overrides` entry. These
stay ONE number: `nrmse`'s `tolerance` (NRMSE has one ceiling: a pair whose
sides differ is that criterion's config error, "nrmse has one ceiling:
tolerance must be a number"), a tube's `tube_min_width` (a floor under both
sides: a pair, a string, `true`, `NaN` or an infinity there is that
criterion's config error, `tube_min_width must be one number, got …`; `null`
is no floor) and the `upper` / `lower` of a tube control point, which is a
side already; and every base tolerance (below).

The pair's keys are exactly `lower` and `upper`, each a finite number.
Anything else in a field of the table — one side missing, a third key, a
string such as `"0.01"`, `true` — is that criterion's config error, naming
the field (`points[0].tolerance must be a number or {"lower": …, "upper": …},
got …`): the criterion fails with that reason and the rest of the test is
scored. No sign is checked: a negative number can never be met, a pair
with one negative side is a band that does not hold its target, and a
negative `tube_min_width` floors nothing. A positive one widens both sides
of a tube to itself, a negative width's too: `"tube_abs": -0.3` over
`"tube_min_width": 0.2` is a tube 0.2 either side of its reference. (The
authoring tool refuses to write a negative tolerance, a tube width whose
sides sum below zero with no positive minimum width under it, and a
negative minimum width.)

One example per metric:

```json
{
  "metric": "points",
  "variable": "tank.level",
  "points": [
    {"time": 5.0, "value": 2.0,
     "tolerance": {"lower": 0.01, "upper": 0.05},
     "time_tolerance": {"lower": 0.5, "upper": 0.1}}
  ]
}
```

The checkpoint passes when the run is between 1.99 and 2.05 somewhere from
t = 4.5 to t = 5.1. A row without a `tolerance` takes the criterion's, number
or pair, in the row's own `tolerance_mode`; a `points` criterion with no rows
checks the final value against the criterion's `tolerance`, strictly on both
sides (`−lower < d < upper`).

```json
{"metric": "event-timing", "variable": "valve.open", "time_tolerance": {"lower": 0.001, "upper": 0.005}}
```

Each event may come up to 1 ms early or 5 ms late. A declared event's own
`tolerance` reads the same way.

```json
{"metric": "dominant-frequency", "variable": "rotor.w", "peaks": [{"freq": 50.0, "tolerance": {"lower": 0.01, "upper": 0.05}, "tolerance_mode": "rel"}]}
{"metric": "dominant-frequency", "variable": "rotor.w", "peaks": [{"freq": 50.0, "tolerance": {"lower": 1.0, "upper": 3.0}, "tolerance_mode": "abs"}]}
```

The first looks for its peak from 49.5 Hz to 52.5 Hz — `f·(1 − lower)` to
`f·(1 + upper)` — the second from 49 Hz to 53 Hz. A peak with no `tolerance`
gets 1 % each side.

```json
{"metric": "scalar", "variable": "tank.level", "quantity": {"reduce": "max"}, "tolerance": {"lower": 0.5, "upper": 0.1}}
{"metric": "scalar", "variable": "tank.level", "quantity": {"reduce": "max"}, "tolerance": {"lower": 0.05, "upper": 0.01}, "tolerance_mode": "rel"}
```

The first lets the maximum be up to 0.5 under the baseline's and up to 0.1
over it; the second up to 5 % under and 1 % over.

```json
{"metric": "tube", "variable": "pipe.p[1]", "tube_width_mode": "band", "tube_abs": {"lower": 0.1, "upper": 0.5}}
{"metric": "tube", "variable": "pipe.p[1]", "tube_width_mode": "rel", "tube_rel": {"lower": 0.01, "upper": 0.05}}
```

The first tube runs from `reference − 0.1` to `reference + 0.5`; the second
from 1 % of `|reference|` under it to 5 % over.

Where a criterion's line on the report names its tolerance, a pair reads as
its two edges, `−1.000e-02 / +5.000e-02`, and the deviation beside it carries
its sign, so the line says which side it lay on. A scalar scored against its
baseline draws what it allows, on the report and in the tool: two dashed
lines, at the baseline's scalar plus `upper` and minus `lower`.

**The base tolerance stays one number.** The base tolerance is what a
criterion with no tolerance of its own is scored with: `comparison.tolerance`
in a spec entry, else the one in the baseline's reference JSON, else the
project's (`tolerance` in `testing.json`, or `--tolerance`). It must be ONE
finite number — not a pair (it is `nrmse`'s ceiling, and `nrmse` is the
default metric), and not a string, a bool, a list, NaN or an infinity. One
that is not is refused, never read loosely and never skipped for the next
one down:

- **The project's** is a config error: one line, `Config error: the base
  tolerance must be a number (testing.json tolerance is "0.01")`, and exit
  code 2 before anything runs; `dstf author` does not open the project.
  `null` is unset.
- **A test's** — its spec entry's or its baseline's, whichever is in effect
  — FAILS the test without scoring a variable, and the reason is the test's
  message: `the base tolerance must be a number; a lower/upper pair belongs
  on a criterion (test_spec.json comparison.tolerance is {"lower": 0.1,
  "upper": 0.2})`. The console, the JUnit `<failure>`, the dashboard row's
  Message, the per-test page ("Not scored: …") and the Test page of `dstf
  author` (the strip's "Not scored: …", the tree line's "nothing scored: …")
  all say it. A test whose simulation failed, or that has no result, keeps
  SIM_FAIL or NOT RUN.
- **Accept refuses it.** `run --accept` and `compare --accept` leave the
  test's reference file untouched, list it under "Skipped N test(s):" with
  the reason and exit 1; the interactive review does not offer `a` for it;
  the tool's Accept is grey for that test, with the reason, and stores the
  others of a selection. Nothing writes one either: an accept never stores a
  base tolerance that is no number, and `spec update` — the tool's Save too —
  refuses a patch that sets `comparison.tolerance` to one.

A test with no `metrics` tree is scored one criterion per tracked variable
its result holds. When the result holds NONE of them — its `variables`
patterns match no name in it, or the model was replaced by one that no
longer has them and still simulates — nothing was scored, and that is not a
pass: the test FAILS the same way, with `the result holds none of the
variables this test tracks (pipe.T[*], pipe.m_flow) — nothing was scored` as
its message, with or without a baseline, and `--accept` skips it with that
sentence. A test with a tree is not asked (each criterion fails by itself:
`variable 'x' not found in the result`), and a simulate-only test passes by
having run.

A tracked variable the BASELINE holds and the run no longer has is a
criterion that was not scored, and the test FAILS: `y: nrmse · against
primary` / `Not scored: variable 'y' not found in the result` — the words a
`metrics` tree's criterion has for it. (With no tree the run used to be
scored on the variables it still held, and passed.) Only what the baseline
holds is asked for: a `variables` pattern that matches nothing on either
side is the backend's log line, and a variable the test no longer tracks is
not missed. `--accept` over such a run is not refused — accepting is how a
variable is dropped on purpose — and says what the new baseline lost:

```
Warning: ref_0042.json (MyLib.Examples.Pipe) no longer holds y — the accepted result does not hold it.
```

A variable taken out of `variables` (or whose criterion was removed) leaves
the baseline at the next accept too, and that is said as what it is — the
run may still write it:

```
Warning: ref_0042.json (MyLib.Examples.Pipe) no longer holds z — the test no longer tracks it.
```

An accept that has both prints both lines, each with its own names.

A **simulate-only** test passes iff its simulation completes: nothing is
compared, no baseline is stored for it, and its report page reads
`Simulate-only: simulation succeeded`. An accept names the ones it passed
over, under its `Accepted N test baselines` line — up to six, then `and N
more`:

```
Accepted 0 test baselines to …/ReferenceResults/Dymola/linux
Simulate-only, nothing to store: MyLib.Tank
```

That is neither a warning nor a skip, and the exit code is what it would have
been; the interactive review prints the same sentence for `a` on such a test,
and `dstf author`'s Accept carries it in its notice. A test is
simulate-only when a recognizer marks
it (`simulate_only`), or when it DECLARES nothing to score — no `UnitTests`
variable, no `variables` pattern, no `metrics` tree and no
`comparison.variable_overrides` entry. `"variables": []`
on a model without the component is that entry, and it is what `dstf spec add
<model>` writes; give it a pattern or a criterion and it is scored like any
other test. Give it in the spec file, or add the test with `dstf spec add <model>
--variables <pattern>`: `dstf author` adds a criterion only to a test that
tracks at least one variable, and on a test that tracks none its page says
"nothing is scored" and has no `+` (a known limit, `docs/ideas.md`).

An override alone does not make it one. `comparison.variable_overrides`
changes how a TRACKED variable is scored, so on an entry that tracks nothing
— `"variables": []` and `{"x": {"mode": "range", "min_value": 100,
"max_value": 200}}` — it scores nothing, and whoever wrote it asked for `x`
to be checked. That test FAILS without a criterion, as a result that holds
none of a test's tracked variables does, and `--accept` skips it with the
same sentence:

```
variable_overrides key(s) name no variable this test tracks (x) — an override changes how a tracked variable is scored, and "variables" (or a "metrics" leaf) is what tracks one; nothing was scored
```

Name the variable in `variables` (the override then scores it), or write the
criterion as a `metrics` leaf.

### The points a baseline stores (`baseline_points`)

Accepting a result stores its trajectory as the test's baseline. A baseline
keeps **every point the simulator wrote** — in order, the two samples of each
event included — unless `baseline_points` gives a number (D153). In
`testing.json` it is the project's value:

```json
{
  "baseline_points": 2000
}
```

- `"all"` (the default, and what an absent key means) — every row of the run.
- **A whole number of 2 or more** — at most that many points. The first and
  the last row are always kept, and both samples of every event while they
  fit (events are thinned evenly only when they alone are more than the
  number); the rest are spread evenly **by row number** — not by time, and
  not by what the signal does. A run with no more rows than the number is
  stored whole: a point is never invented.

**One test's own value.** A `test_spec.json` entry may carry the key too, at
its top level beside `simulation` and `comparison`; it takes the same two
forms and is that test's value:

```json
{
  "model": "MyLib.Examples.LongTransient",
  "variables": ["pipe.T[1]"],
  "baseline_points": 5000
}
```

The order is **the test's, then `testing.json`'s, then `"all"`** — so one
long run can be thinned in a project that keeps everything, and one test can
say `"all"` in a project that gives a number. It is written by hand:
`spec update` and `dstf author` do not edit it, and the tool's Save keeps it
on an entry it rewrites.

Anything else — `true`, `2000.0`, `0`, `1`, a negative, `"ALL"`, `null` — is a
config error in either file: one line, `Config error: testing.json
baseline_points is 1: the points a baseline keeps are "all" or a whole number
of 2 or more` (for an entry, `<test_spec.json>:
tests[MyLib.Examples.LongTransient].baseline_points is 1: …`), and exit code 2
before anything runs. `spec schema` lists the entry's key.

**What the stored points mean for scoring.** A baseline is its stored points
and nothing else. `nrmse`, the default mode, compares the run **at the
baseline's time points**: the run is read there (interpolated between its own
samples), and the score is the root mean square of the error over those
points, each counting once, divided by the baseline's range. So the stored
points are both the resolution at which a regression can be seen and the
weighting of the score: what a run does between two stored points is not
looked at, and a stretch the solver sampled densely counts for more than a
sparse one. Thinning a baseline changes both — a spike between two kept
points is gone from it.

**Changing the key changes nothing in an existing baseline** until its test
is accepted again: a baseline holds the points it was stored with, and a
compare reads them as they are. Each reference JSON says what its file holds,
under `sampling`:

```json
"sampling": {"setting": "all", "source_points": 5021, "stored_points": 5021, "precision": "single"}
```

`setting` is the `baseline_points` in effect for the test when it was accepted,
`source_points` the rows the run had and `stored_points` the points stored. A
reference with no `sampling` was stored before the key existed, when every
baseline was thinned to at most 2000 points; it loads and scores as it always
did. To keep storing baselines that way, set `"baseline_points": 2000`.

**A baseline holds its run's own numbers.** `precision` says what the run's
arrays were. A `"single"` baseline — Dymola writes single-precision result
files — is written with 9 significant digits, the fewest that identify a
single-precision number, and read back through single precision: each stored
value is the run's, bit for bit, so a baseline scores exactly `0` against the
run it was accepted from. A `"double"` one is written with 17, the fewest
that identify a double — each at its shortest spelling, so `0.1` is still
`0.1` — and is the run's bit for bit too. A reference without the field was
written with 7 digits for a single-precision run, and a `"double"` one
stored by an earlier version holds 15 (not the run's own doubles: it scores
up to about 1e-9 against its own run where the signal's range is small
beside its level); each is read as the numbers its
file spells, as before, and scores as it did until its test is accepted
again. A run of mixed precision is `"double"`, and its single-precision
arrays are then the doubles their 9 digits spell.

**A large baseline is said when it is accepted.** Keeping every point of a
very long run makes a large file. When a baseline file just written is
larger than 5 MB, the accept says so, once per file:

```
Warning: ref_0123.json (MyLib.Examples.LongTransient) is 5.3 MB (74,167 points × 3 variables) — set "baseline_points" for this test to store fewer.
```

It is a warning, as `no longer holds …` is: the baseline IS stored, and the exit code is what it would
have been. `run --accept` and `compare --accept` print it under their
`Accepted N test baselines` line, the interactive review under its
`Accepted (…)`, and `dstf author`'s Accept carries the same sentence in its
notice. The threshold is fixed — there is no key for it.

### Multiple named baselines

A test can be scored against more than one baseline: the `primary` baseline (what the framework writes on `--accept`, stored at the flat top level of `ref_NNNN.json`) plus any number of named soft_checks, each its own file under `<reference_root>/<Backend>/<os>/soft_checks/ref_NNNN/<name>.json` — `experiment`, `analytical`, `dymola`, etc. MetricTree leaves pick which baseline to score against via `"against": "<name>"` (default is `"primary"`):

```json
{
  "model": "MyLib.HeatExchanger",
  "variables": ["T"],
  "metrics": {
    "combinator": "and",
    "children": [
      {"metric": "nrmse", "variable": "T", "tolerance": 0.01},
      {"combinator": "warn", "children": [
        {"metric": "nrmse", "variable": "T", "against": "experiment", "tolerance": 0.1}
      ]}
    ]
  }
}
```

Soft_checks are added from the CLI with `dstf soft-check add <model> <name> <source>`, or programmatically:

```python
from dstf.config import Config
from dstf.storage.reference_store import ReferenceStore

config = Config(config_file="path/to/testing.json")
store = ReferenceStore(config)
store.add_soft_check(
    model_id="MyLib.HeatExchanger",
    name="experiment",
    time=[0.0, 1.0, 2.0],
    variables=[{"index": 1, "name": "T", "values": [300.0, 310.5, 315.2]}],
    provenance={"origin": "rig-run-2026-04-17", "citation": "Report Q2"},
)
```

The model must already have a primary baseline (run with `--accept` once). A soft_check is a separate file, so accepting fresh primary results never touches it.

### Companions

A companion is data shown on the test's plots and never scored — rig logs,
an analytical solution, another library's run of the same model. It is
registered against a model that already has a primary baseline, and stored
as metadata under `<reference_root>/<Backend>/<os>/companions/ref_NNNN/<name>.json`
pointing at the file (`companion freeze` copies the data in beside it).

```bash
# A rig export, with the columns it draws on named
uv run dstf --config testing.json companion add MyLib.HeatExchanger rig ./rig.csv \
    --map "unitTests.x[1]=T_in" --map "unitTests.x[2]=T_out"

# What is registered, and what each companion draws on
uv run dstf --config testing.json companion list MyLib.HeatExchanger
```

The Modelica example ships both kinds, and each is a curve of the SAME model
as the test it is drawn on. `FrequencyTest`, `RangeCheckTest` and
`MultiFrequencyTest` carry, as a companion in the `OpenModelica/linux`
partition, the Julia example's baseline of that model (`julia-mtk-frequency`,
…) — a pointer to another library's reference file, mapped by name; it lies
on the simulated curve to 0.3 % of its range, two solvers apart. The Julia
example's `Frequency`, `RangeCheck` and `MultiFrequency` carry the
OpenModelica baseline the other way round (`modelica-om-…`). The tests of
the two libraries that share a name and not a model (`Constant`,
`SimpleRamp` / `SimpleTest`, `TubeTolerance`, `MetricTree`) carry no such
companion: it would draw another model's curve. Neither Dymola partition
carries a Julia companion. `MetricTreeTest` carries
`analytic`, in `Dymola/linux` and `OpenModelica/linux` (`Dymola/windows`
ships no companion): the closed-form solution of the variable its criteria compare
with a baseline (`x = 1 − e^(−2t)`) written to a CSV on a fine grid, registered with
`--map "unitTests.x[1]=x"` and then frozen, so the
data sits beside the baseline it is drawn on (`companions/ref_NNNN/
analytic.{json,csv}`). On the report page, tick `analytic` in the plot's
Overlays row: it lies on the simulated curve to 3e-6. Map a companion's
variable to a variable the test's criteria SCORE: an overlay is drawn on
that variable's plot, and a watch plot (a `plots` entry on an unscored
variable) draws the run's series alone.

**Say which variable is which.** `--map TEST_VAR=COMPANION_VAR` is repeatable
and is the record the report reads: the left name is a variable of the test
(the name its result file gives it — `unitTests.x[1]`, not the expression the
model wrote), the right one is a variable of the companion's file (a JSON
companion's `variables[].name`, a CSV companion's column header). Both are
checked when you register, against the stored baseline and against the file,
so a typo is refused there and then rather than drawing nothing months later.
Two test variables may name one companion variable — that draws the one curve
on both plots. One test variable may not be named twice.

Where a companion has a mapping, that is the whole rule: a variable the
mapping does not name draws nothing from it, even if the companion happens to
hold a variable spelled exactly like it. **Map a companion whenever the two
sources name the quantity differently**, which is every companion of a
Modelica `UnitTests` test, since its variables are named positionally.

A companion with no mapping falls back on spellings — that is what the
companions registered before mappings existed do. In order: the companion
variable whose name is the test variable's name, whose name is its expression,
and whose own recorded expression is either. If a step finds two candidates the
companion draws nothing for that variable and the report's overlay table says
so, naming the candidates and the `--map` that settles it.

**Only a companion falls back.** Soft_checks and the auto-discovered
sibling-backend overlays are DSTF's own data under the test's own names, so
they match by name and nothing else — a soft_check is scored by name
(`against:`), and the curve drawn for it has to be the curve scored for it. A
companion whose `variables` record has been hand-edited into something
unreadable does not fall back either: the overlay table says what was wrong
with it, and it draws only the entries that survived reading — a record, even
a broken one, is an answer, and guessing behind it is still guessing.

---

## Commands

The commands are `discover`, `run`, `compare`, `author` and `check`, and five
groups whose actions are commands of their own: `spec add` / `update` /
`schema`, `baseline export`, `soft-check add` / `list` / `remove`,
`companion add` / `list` / `freeze` / `remove` and `manifest show` / `dump` /
`cleanup`. `dstf --help` lists them; `dstf <command> --help` (`dstf manifest
cleanup --help`) says what one does, what it writes and every flag it reads,
with examples. A group with no action (`dstf spec`) prints its help and exits 2.

Four flags say where the project is:

```
--config FILE          The project's testing.json (default: searched for)
--source-path DIR      The library under test (Modelica package dir, FMU dir, ...)
--reference-root DIR   The reference results root
--test-spec FILE       The test_spec.json
```

They work on **either side of the command** — `dstf --config x run` and
`dstf run --config x` are equivalent. A value given after the command wins.

**A command offers the flags it reads, and a flag that does not apply is an
error**, on either side of the command — never dropped. `author` takes
`--config` alone of the four (it saves the spec `testing.json` names);
`check`, `baseline export`, and the `soft-check`, `companion` and `manifest
show` / `dump` actions take no `--test-spec`; `spec schema` reads no project
and takes none. `--backend` is on every command that runs a simulator or reads
a reference partition (all but `discover`, `check`, `spec add` and `spec
schema`), `--work-dir` on those that read or write the work directory (`run`,
`compare`, `author`, `baseline export`, `manifest dump` / `cleanup`); both go
after the command. `--timeout`, `--parallel` and `--port` take a whole number
of 1 or more, and `--output` beside the console report format — which writes
no file — is an error.

If `testing.json` includes `source_path`, you only need `--config` or `--reference-root`.

### `discover` — Find tests

Lists all discoverable tests without running anything.

```bash
# All tests
uv run dstf --config testing.json discover

# Filter by package
uv run dstf --config testing.json discover --package MyLib.Fluid

# Filter by glob pattern
uv run dstf --config testing.json discover --filter "*Pipe*"
```

Output shows, per test, its model ID, what it tracks, its stop time, its solver method and its source (unit_tests, spec, or both). **Vars** is the count of the test's `UnitTests` variables, and its `variables` entries as patterns — `2`, `1 pattern`, `2 + 3 patterns`: a pattern names no variable until it is matched against a result, after the run. **Method** is `—` for a test that names no solver on a source other than Modelica: the default, `Dassl`, is what a Modelica tool is given, and not something an FMU, a Julia or a Python test uses. The totals count the same two things, `UnitTests variables` and `Variable patterns`.

### `run` — Simulate and compare

Runs simulations and compares against stored reference baselines.

```bash
# Run all tests, report pass/fail
uv run dstf --config testing.json run

# Run a subset
uv run dstf --config testing.json run --package MyLib.Fluid

# Run with 4 concurrent simulation workers
uv run dstf --config testing.json run --parallel 4

# Accept all results as new baselines (first run, or after intentional changes)
uv run dstf --config testing.json run --accept

# Interactive review — decide per test
uv run dstf --config testing.json run -i
```

**Options:**

| Flag | Description |
|------|-------------|
| `--accept` | Store all results as new reference baselines |
| `-i`, `--interactive` | Review each test interactively |
| `--parallel N` | Number of concurrent **simulation workers** (any backend, not just Dymola). Note: the same flag on `compare` means report-**rendering** threads — see below. |
| `--backend NAME` | Override the backend (e.g., `"Dymola 2025"`) |
| `--backend-path PATH` | The backend's executable, in place of the one `testing.json` names or the search finds. One that is not there is not found: no other install is run in its place |
| `--tolerance FLOAT` | Override the project's comparison tolerance (`testing.json`'s `tolerance`); one finite number |
| `--default-points` | Use PointsMode by default (empty list ⇒ final-value-only check) |
| `--timeout SECS` | Per-test timeout in seconds, a whole number of 1 or more (default: 60) |
| `--batch` | Run through the backend's script mode instead of its workers that stay alive (Dymola, OpenModelica, Julia). On FMPy and Python, which have no such workers, it is a `Config error` (exit 2) before anything runs |
| `--work-dir PATH` | Override output directory |
| `--report` | Also write the per-test pages under `<work_dir>/reports/` (see "Live progress + final report") |
| `--report-format` | `junit` or `html` (the dashboard page) also writes that file, to `--output` / `-o` (default `test-results.xml` / `test-report.html` in the current directory); `console`, the default, writes none, and `--output` beside it is an error |
| `--no-browser` | Open no browser tab. `run` opens the dashboard as the run starts, `--report` (on `run` and `compare`) opens it again once the pages are written, and `p` in the interactive review opens that test's page; with this flag each only prints its `file://` link. Also on `compare`. |

A run that scores (not `--accept`, not `-i`) always prints the verdict table —
a `PASS` / `FAIL` line per test, `Results: 11/12 passed` and the failure
details: under each failed test one block per criterion that did not pass, in
the tree's order. A block's first line says which criterion it is — the
variable, the metric, its window if it has one and the baseline it reads,
where it reads one (`x: range` names none; a criterion of a test with no
`metrics` tree names `primary` like any other) — and starts `advisory — `
when the criterion is under a `warn` and
cannot fail the test; the next line is the measured value against what it is
held to, in the metric's own words, then that metric's details:

```
  [0007] ModelicaTestingLib.Examples.MetricTreeTest
    x (unitTests.x[1]): nrmse · window to 2.5 s · against primary
      NRMSE 4.264e-02 vs tolerance 1.000e-03 → FAIL
      RMSE:               4.235140e-02 (range 9.9326e-01)
      max |err|:          6.697616e-02 at t=0.54
      final:              ref=9.932612e-01  act=9.844956e-01
    advisory — x (unitTests.x[1]): nrmse · against openmodelica
      NRMSE 2.128e-02 vs tolerance 1.000e-02 → FAIL
      RMSE:               2.127812e-02 (range 1.0000e+00)
      max |err|:          6.697616e-02 at t=0.54
      final:              ref=1.000000e+00  act=9.999999e-01
```

The metric a block names is the one its criterion was scored by — the same
name as the leaf's `mode_effective` in `verdicts.json` — also where the
criterion could not be scored: an override written `{"mode": "range"}` is
headed `x: range` over `Not scored: needs at least one of min_value /
max_value`. An override naming a mode nothing is registered under has no
metric: its block is headed by the variable alone.

A test whose only failures are under a `warn` PASSES, and says so: its line
reads `PASS  [0007] MyLib.Examples.Rod (1 advisory failure)`, the summary
counts such tests (`With advisory failures: 1`), and a section after the
failure details — `Advisory Failures (under `warn`: they fail no test):` —
prints their blocks. The exit code is not changed by them.

`--report` and `--report-format` each add what they name, and
together they add both: `run --report --report-format junit --output
results.xml` prints the table, writes the pages and writes the file. The three
flags do the same on `compare`.

**Interactive mode (`-i`)** prints each test's status and, where it was
scored, `Criteria: 2/3 passed, 1 failed` — its criteria, the Criteria
column's count (two criteria on one variable are two). A failure under a
`warn` is not counted as one that fails the test: `Criteria: 1/3 passed, 1
failed, 1 advisory failure`, and `Criteria: 2/3 passed, 1 advisory failure`
for a test that passes with one — the counts the failure blocks above and
JUnit's message are written from. Its actions:

| Key | Action |
|-----|--------|
| `a` | Accept this test's results as new baseline (through the shared accept path, so it refuses what `--accept` refuses) |
| `s` | Skip (don't update baseline) |
| `d` | Show detailed per-variable comparison |
| `p` | Generate plots and open in browser |
| `v` | Add variable patterns to track (updates test_spec.json) |
| `q` | Quit interactive review |

`a` stores through the same function `run --accept` and `compare --accept`
call, so it accepts exactly what they accept. A test the last run left
recorded as pending or cancelled — a run aborted or stopped while it was in
flight — is refused with
`re-run before accepting`; a result the store declines (a trajectory carrying
`NaN`, say) is refused with that reason and the review moves on to the next
test; and a simulate-only test stores no baseline at all
(`Simulate-only, nothing to store: MyLib.Tank`, the line `--accept` prints
for it), since there is nothing to compare
it against. A test the comparison refused — a base tolerance that is not one
number — prints its reason as `Error: …` and is not offered `a`.

### Live progress + final report

DSTF writes a single `dashboard.html` to your `work_dir` that serves as both the live progress page during a run and the final report after comparison.

- **During a run**: the page is re-written on every state change and reloads itself every 5s (a meta refresh — a script fetch is blocked on `file://`). It stays live until the command that started the run has finished with it.
- **After comparison**: the same page is re-rendered in final mode — the refresh is dropped and each row carries its verdict and its numbers (Worst NRMSE — the worst among the test's criteria that are scored by `nrmse`, `—` for a test with none: a tube's, a range's or a checkpoint's own number is not an NRMSE and is not shown as one — Criteria (passed / in all: the criteria of the test's tree, so two on one variable count as two, an array's twenty elements as twenty, and an advisory one counts), Warnings) and its Translate / Sim / Total wall times. The Reference link comes from the per-test report, so it is filled only with `--report`, which also writes the per-test interactive deep dives at `reports/ref_NNNN/interactive.html` (`reports/test_NNNN/` for a test with no baseline), linked from the Test column. A name is a link only where this page read that test's report: a run clears the report pages of the tests it runs, so after a plain `run` or `compare`, and for a test the queue of `dstf author` ran, the name is plain text until a `--report` writes its page again. A `run` or `compare` that removed pages and is not writing them again says so in one line: ``Removed 12 per-test report pages written by an earlier --report (now stale); `compare --report` writes them again.``
- **Which rows**: a live page lists the tests the running run holds. A final page lists the last run's tests and then every other test the command scored, in the work dir's manifest order. So `compare` after a `--filter` run, a `--merge` run, and a drained queue in `dstf author` list every test with its verdict; a plain `--filter` run without `--merge` lists only its own tests, because those are all it scored. A test the last run did not run keeps its per-test report (and its Reference link) while that report is newer than the test's own last run. The status bar of such a page names both counts: `Final report · 331 tests · last run: 1 test in 0.4s` — for a drained queue, the time from its first test's start to its last one's end.

`status.json` and `batch_manifest.json`, beside the dashboard in the work dir, record the RUN: a test's `status` in `status.json` and its `last_run_status` in `batch_manifest.json` say whether its simulation ran. `"passed"` there means the simulation finished — not that the comparison passed. The comparison's verdict is the test's `verdict` in `status.json`, beside that `status`, once a `run` or `compare` has scored it — and in `verdicts.json`, on the console, in the JUnit file and on the dashboard (see "Reading results from a script").

### Reading results from a script

Four files say what a run decided; none needs the HTML parsed.

| File | Written by | Holds |
|---|---|---|
| `<work_dir>/verdicts.json` | every `run` and `compare` that scores (not `--accept`, not `-i`), with or without `--report` | one entry per scored test — `model_id`, `test_id`, `status` (the console's word: `PASS`, `FAIL`, `SIM_FAIL`, `NO_REF`, `NOT RUN`), `message` (a failed simulation's error, a refusal), `warnings`, and `tree`, its criteria tree — plus `totals` by status word, the `exit_code` they give, and the run's `metadata` (backend, tool version, os) |
| `<work_dir>/status.json` | the run, as it goes; the scoring command adds the verdicts | per test: `status` — what became of its SIMULATION (`queued`, `running`, `passed`, `failed`, `timed_out`, `cancelled`) — `verdict`, the comparison's word (`null` until it is scored), `phase` (`"done"` once its run has ended, however it ended — a test its backend could not start, for want of its source file, is `failed` and `done` with the reason in `detail`; only a test that was never run stays `queued` with `phase` `null`), `elapsed`, `worker_id` |
| JUnit XML | `--report-format junit --output results.xml` | one testcase per test; a `<failure>` per FAIL with its failed criteria, an `<error>` per failed simulation |
| `<work_dir>/reports/<dir>/comparison_data.json` | `--report`, one per test | that test's `summary` and its `tree_view` — the same tree as in `verdicts.json` — beside what its page draws |

A node of `tree` is a group (`"kind": "combinator"`, with `combinator`, `children`, `passed`) or a criterion (`"kind": "leaf"`): `path` (the JSON pointer a `spec update` patch names it by), `metric`, `variable`, `window`, `against` (the baseline it READ — `"primary"`, a soft check's name — and `null` for a criterion that reads none: a `range`, a `scalar` with bounds, a criterion scored from its own declared events, peaks or values), `passed`, `advisory` (true under a `warn`: it cannot fail the test), `score` and `score_display` (its measure), `tolerance` (the tolerance it was scored with — its own, else the test's — for a metric that takes one; a tube's width and a range's bounds are in `params`), `criterion` (the sentence the console prints) and `details`. `tree` is `null` for a test nothing was compared for.

```bash
dstf --config testing.json run --no-browser
python - <<'PY'
import json
doc = json.load(open("testing_output/MyLib/Dymola/linux/verdicts.json"))
print(doc["totals"])
for test in doc["tests"]:
    if test["status"] == "FAIL":
        print(test["model_id"])
PY
```

`verdicts.json` holds the tests the last scoring command scored — a `--filter`ed run's are the filtered ones. It is removed, like the per-test pages, by the next run or compare of the work dir (and by a queue run of `dstf author`), which writes it again if it scores.

The **Translate (s) / Sim (s) / Total (s)** columns — on the dashboard and on the Suite page of `dstf author` — are wall-clock seconds. Total is always the whole test; what Translate and Sim time depends on the backend:

- **Dymola** (persistent, the default): DSTF's own clock around the translate call and around the simulate call.
- **OpenModelica**: omc's own record — Translate sums its frontend, backend, code-generation and compile phases, Sim is its simulation phase.
- **Julia** (persistent, the default): the Julia worker's own time for the simulate call, as Sim.
- **FMPy, Python, Julia `--batch`**: the whole test's wall, as Sim — there is no translation step to time — so Sim equals Total.
- **Dymola `--batch`**: none — a `.mos` batch times the batch, not each test, and a per-test average would show a time no test took.

A run keeps these, and its other statistics, beside each result in `test_NNNN/run_stats.json`, so a later `compare`, a `--merge`d test and a reload in `dstf author` show them too. A Dymola result from before that file existed still shows its translation and simulation statistics, read back from the `dslog.txt` and `translation_log.txt` beside it; the logs hold no wall times. Where no wall time was kept — a test last run before that file existed, or a Dymola `--batch` run — Sim (s) shows the result file's own CPU time instead, marked: the number is followed by a small **cpu** tag and its tooltip reads "CPU time from the result file (no wall time was kept)". That is the final value of Dymola's `CPUtime` (DSTF sets `OutputCPUtime := true`), so only Dymola results carry one; on any other backend such a row reads "—" until it is re-run. A kept wall time always wins, and Sim (s) sorts and filters on the number whichever it is.

A row reads **SIMULATED** when its simulation ran and nothing has scored it: during a run, between a test finishing and the compare phase, and on a page written without scoring at all — after `run --accept` (the results were stored as baselines, not compared), or an aborted run. SIMULATED is not a status: it is in no count pill.

A row reads **NOT RUN**, in grey, when its test has no result: the last run never ran it, or started it and never finished it (a run stopped by Ctrl-C or a crash leaves its unfinished tests so). That is not a failed simulation — a test whose run finished and failed, timed out or could not translate reads SIM_FAIL — and it is not a failure: `run` and `compare` exit 0 when every test that ran passed, whatever is NOT RUN beside them — but when NO test ran (every test in scope is NOT RUN, or none is in scope) they exit 1 and say so on stderr (`Nothing ran: no test in scope has a result (3 NOT RUN) — exit 1.`): a run in which nothing ran is not a green one; the console lists it as `NOT RUN` and JUnit writes it as a skipped testcase (`<skipped message="not run"/>`). Run it again to give it a verdict. A run the tool's queue cancelled is recorded as cancelled and reads **CANCELLED**, also grey, on every dashboard written while `status.json` still lists that run — the queue's own and a later `compare`'s: it left no result either, so `compare` scores the test NOT RUN, the NOT RUN pill counts it, and the console and JUnit, which have no row to say why on, list it as NOT RUN.

The count pills along the top are the status filter: click one to show only its rows (PASS, FAIL, SIM_FAIL, NO_REF, TIMED OUT, QUEUED, RUNNING, NOT RUN; `warnings` shows the rows with any), `Total` to show every row again.

Every column but Status has a typed filter under its header:

- **Text columns** (Key, Reference, Test, Resolution, Worker, Message) match a **substring** by default, case-insensitively. A query containing `*` or `?` is a **glob** matched against the whole value (`*Pipe` matches `GenericPipe`, not `GenericPipe_Test`; `[` and `]` are literal). With the **`.*`** toggle pressed every text filter is a **regex**, searched (a bad one marks its box and filters nothing); with **`Aa`** pressed every text filter matches case. A row with no value in the column reads as empty to its filter, not as the `—` its cell shows — so a regex `.` on Message shows the rows that have a message.
- **Number columns** (Elapsed, Worst NRMSE, Criteria, Warnings, Translate, Sim, Total) take a comparison — `>x`, `>=x`, `<x`, `<=x`, `=x` (a bare number is `=`) — or an inclusive range `a..b` (`1e-6..1e-4`). They compare the value, not its display text; a row with no value never matches. Criteria filters on the number passed.

Every column sorts on a 3-state cycle (none → sorted → reverse → none). Numeric columns sort descending first (largest first when triaging failures); text columns sort ascending first. A row with no value sorts last both ways.

The **Resolution** column shows where each test's simulation parameters came from: `annotation` (from the `experiment(...)` annotation in the .mo file), `test_spec` (overridden in test_spec.json), or `mixed` (some fields from each — hover to see the per-field breakdown). This is the resolution-explainer for the two-layer contract documented above.

#### The per-test page

`interactive.html` is a **read-only** view of one test. It mutates nothing and
computes nothing: every number on it comes from a figure Python built or from
markup the server rendered. It shows

- one Plotly chart per plot of the test, the plots the authoring tool draws
  (see "Plot layout"): one per scored variable or family in the tree's order,
  then the watch plots; with nothing scored — no baseline, or simulate-only —
  one per tracked variable or array, except that a test with no baseline and
  an authored `metrics` tree plots the tree's variables; a run that failed,
  none. The entry's `plots` key
  adds only extras: context series on those plots, and watch plots. Under
  each chart are its criteria in tree order, each led by its ancestors'
  kinds (`and › k-of-n ›`), its context series listed muted ("Context: …"),
  and a watch plot says "No criteria yet.". A family's badges name their
  members (`pipe.T[2] FAIL`); a variable the tree scores and the result
  lacks keeps its badge and its plot says "Not in this result: …" — with no
  chart when the plot's whole subject is missing. Each
  chart draws actual, reference and the criterion geometry
  the metrics emitted (a tube's band, a range's bounds, a scalar's bounds
  or — scored against its baseline — the two lines of what it allows, a
  points leaf's tolerance boxes, event windows, the FFT spectrum on its own
  axis pair, a scoped leaf's window as a shaded band). Zoom, hover and legend toggles are
  live, which is how you read values off a plot. The legend sits above the
  plot area, and Plotly's toolbar stands vertically in a gutter right of the
  plot. A section's heading reads
  its variables' labels, with their result-file names muted beside it when
  the two differ, and `data-variable` on the markup is always the name;
- an **overlay picker** above any plot of one variable a soft_check or
  companion carries data for — the overlay traces are already in the figure, and the
  checkbox toggles their visibility;
- an **error-overlay** dropdown per plot of one scored variable the result
  carries: signed error,
  absolute error, or per-point NRMSE. Signed and absolute error share the right axis
  "Error [signal units]"; NRMSE, dimensionless, has its own, "NRMSE [–]". Each
  axis is shown while a series on it is visible, whether the select or a legend
  click turned it on;
- the **metric tree**, with the CLI's verdict per node, a one-line criterion
  under each leaf, and the leaf's detail pairs (what was measured against what)
  — plus a summary line counting leaves passed and flagging advisory failures
  under a `warn` wrapper. A leaf reads `metric · <label>`: the expression its
  model wrote where there is one, the name otherwise;
- statistics, diagnostics and artifact sections, and plots for diagnostic
  variables. The Statistics table is the rows `dstf author`'s Statistics card
  shows: a row is tinted when the criterion its `+` would add would fail (a
  count that moved; a time beyond 10%), and a count a baseline never wrote
  because its list was empty reads 0;
- a **decimation banner** naming every trace the page thinned, with its full and
  embedded sample counts, when any was thinned. `max_embedded_samples` in
  `testing.json` sets the cap; the full-resolution data is in
  `comparison_data.json` beside the page.

Changing a criterion is not something the page does — edit `test_spec.json`, or
apply a patch with `spec update`.

`spec update` reads a JSON-Patch envelope in either of two shapes. The
multi-model form carries any number of models:

```json
{"patches": [
  {"model": "MyLib.Examples.SimpleTest", "patch": [
    {"op": "replace", "path": "/comparison/tolerance", "value": 0.01}
  ]},
  {"model": "MyLib.Examples.Other", "patch": [
    {"op": "add", "path": "/comparison/variable_overrides/tank.level",
     "value": {"mode": "range", "min_value": 0.0, "max_value": 2.0}}
  ]}
]}
```

The single-model form — `{"model": ..., "patch": [...]}` — is read too, because
files in that shape exist. The authoring tool writes only the multi-model form.
Paths in each `patch` are relative to that model's entry in `test_spec.json`
and may only touch `/comparison`, `/metrics` and `/plots`; anything else —
`/variables`, `/simulation` — is refused, so a patch cannot change what a test
tracks or how it runs. A patch that leaves `plots` invalid writes nothing.

#### Report layout and offline use

`--report` writes one directory per test plus a single shared copy of Plotly:

```
work_dir/
├── dashboard.html                    # draws no charts — needs no Plotly
└── reports/
    ├── plotly.min.js                 # ONE copy per suite (~1.25 MB)
    ├── ref_0001/
    │   ├── interactive.html          # loads ../plotly.min.js
    │   ├── interactive.js            # built from the packaged report.js
    │   └── comparison_data.json      # full-resolution trajectories
    └── ref_0002/…
```

Plotly ships with the report rather than loading from a CDN, so the pages render fully on a machine with no network (air-gapped labs included). It is stored **once per suite**, not once per test — a per-test copy cost 1.25 MB × N (≈ 414 MB for a 331-test suite).

**Portability cost of the shared copy**: `reports/` as a whole moves, zips, or copies cleanly. A *single* test's directory does not — `reports/ref_0007/` on its own has a dangling `../plotly.min.js` and renders without plots. Copy `plotly.min.js` in beside it (then edit the tag to `plotly.min.js`), or copy the parent directory instead.

`--report-format html --output <path>` writes the dashboard page anywhere; because that page draws no charts it renders fully wherever it lands. Its per-test row links (and the Plotly those pages load) are relative to the work dir, so they only resolve from inside it — the command prints a note saying so.

### `compare` — Re-compare without simulating

Uses the last simulation results (from `testing_output/`) and compares against current references. Useful after changing tolerances or updating references externally.

```bash
uv run dstf --config testing.json compare

# With different tolerance
uv run dstf --config testing.json compare --tolerance 0.001

# Only check final values (default mode = PointsMode with empty list)
uv run dstf --config testing.json compare --default-points

# Output as JUnit XML (for CI)
uv run dstf --config testing.json compare --report-format junit
```

### `baseline export` — Dump reference data

Exports the stored baselines of one partition to JSON or CSV for external
inspection: `references.json` or `references.csv` in the work directory unless
`--output` / `-o` names a file. `--backend` picks the partition, `--work-dir`
the directory.

```bash
# Export all references as JSON
uv run dstf --config testing.json baseline export

# Export as CSV
uv run dstf --config testing.json baseline export --format csv

# Export to specific file
uv run dstf --config testing.json baseline export --output results.csv --format csv
```

### `manifest` — Manage test IDs

Each test gets a stable numeric ID (e.g., `ref_0001.json`). The manifest tracks the mapping between IDs and model paths.

```bash
# Show all active tests and their IDs
uv run dstf --config testing.json manifest show

# Also show obsolete (retired) tests
uv run dstf --config testing.json manifest show --show-obsolete

# List reference files for obsolete tests (dry-run by default; --apply deletes)
uv run dstf --config testing.json manifest cleanup [--apply]

# The same for a work directory's leftovers of models no longer declared
uv run dstf --config testing.json manifest cleanup --orphans [--apply]

# Write the ref ID → model ID mapping to the work directory
uv run dstf --config testing.json manifest dump
```

(The reference index is rebuilt automatically on every command — there's
no separate "rebuild" action because there's nothing to rebuild.) `--orphans`
and `--apply` are `manifest cleanup`'s and `--show-obsolete` is `manifest
show`'s: given to another action, each is an error.

### `spec add` — Add test to spec

Adds a model to `test_spec.json` without manually editing the file. (The
group's other two actions are `spec update`, which applies a JSON-Patch
envelope — see "The authoring tool" — and `spec schema [test_spec|testing]`,
which prints the JSON-Schema of `test_spec.json` or of `testing.json`.)

```bash
# Add a test with specific variables
uv run dstf --config testing.json spec add MyLib.Examples.NewTest --variables "pipe.T[1]" "tank.level"

# Add simulate-only (no variable tracking)
uv run dstf --config testing.json spec add MyLib.Examples.NewTest
```

The second form writes `{"model": …, "variables": []}`: an entry that
declares nothing to score, so the test passes when its simulation completes
(see "simulate-only" under the comparison settings above).

---

## The authoring tool

`dstf author` opens a local browser tool over the same project every other
command reads: the test list, the figures the report page draws, the scored
metric tree, and the capabilities that edit `test_spec.json`. It needs the
`[author]` extra (NiceGUI and `regex`); without either the command exits with
the install hint.

```bash
uv sync --extra dev --extra author --locked
uv run dstf --config testing.json author
```

`--config` is required. The tool edits and saves the spec that file names, so
`--source-path`, `--reference-root` and `--test-spec` are
refused rather than pointing it at a different spec than it shows. `--filter`
and `--package` narrow the test list; `--work-dir` and `--backend` choose the
run outputs and the reference partition to read; `--port` moves the server off
8080; `--no-browser` serves without opening a tab. The server binds `127.0.0.1`
only, one project per process, and runs until Ctrl-C or Stop server. Each
browser tab keeps its own selection and its own open pages, so two tabs on one
server do not move each other's plots.

**Opening a project.** The library scan is cached across restarts under
`$XDG_CACHE_HOME/dstf/scan/` (`~/.cache/dstf/scan/` when `XDG_CACHE_HOME` is
unset), one file per library path and recognizer set — never in the work dir. A
cache another DSTF version wrote is ignored and replaced. The cache is trusted without checking a single source file, so a
cached open skips the scan; the tool then rescans the library in the background
and re-merges any test whose scan changed. Only a Modelica source is scanned, so
only it is cached. The index of sibling-backend overlays is built on its own
thread as the project opens, so a click normally finds it already built.

**The layout.** Two pages under one header. The header carries the library, the
backend, the open's progress, the pending-op count (click it for the preview,
below), Save, Discard, Export, a link to the dashboard and Stop server.

**The theme.** The tool opens in the project's theme (`"theme"` in
`testing.json`: `default` or `dark`). The header's ⋮ menu holds an item named
for the other one — **Dark theme** or **Default theme** — which switches this
browser tab at once: the page and its plots (a zoom, an open criterion's rings
and arrows and a staged edit stay as they are). The tab's other page opens in
the same theme, and a reload of the tab keeps its choice; another tab, and the
next start of the tool, begin in the project's theme again. The switch touches nothing on disk: the dashboard
and the report pages stay in the theme they were generated in. On `dark`, a
status row and a status pill are dimmed — the status's colour laid thin over
the dark surface, with its light shade as the text — while a filled badge is
the same in both themes; a plot's background, grid, axes, legend and hover
label follow the theme, and its series keep their colours except eight that
would not read on a dark plot (the soft-check, sibling-backend and companion
overlays, the actual's event line and the darkest shades of an array's colour
ramp), which are lighter there.

**The preview** is the pending-op count's drawer: per edited test, in staging
order, its entry before and after its edits as `test_spec.json` will hold it
(`json.dumps(indent=2)`, what Save writes), added lines green and removed lines
red, each variable read by its label with its result-file name muted beside
it; how many edits the test has and what the edits do to its verdict ("2
edits · verdict would change PASS → FAIL"). When a test's first edit wrote its
implicit tree out, the diff is against that tree, and a note says Save writes
the tree too ("Save also writes this test's 3 criteria out as a tree; they
score the same."). Edits that leave a test's entry as the file holds it are
not pending at all — a value typed and cleared again, a refused value put
back, a context series added and then taken away by Reset layout: the
header's count, the strip, the Tests drawer's mark, Save and this preview do
not hold them, and the test is back at its saved verdict. The tree written
out goes the same way once the edit that needed it is put back, whatever else
is pending: a watch plot added beside a tolerance typed and cleared is one
edit, and Save writes the `plots` and no `metrics`. One edit keeps it: a
change to the test's per-variable overrides (`comparison.variable_overrides`).
The implicit tree is built from those, so with one edited the tree written
out is no longer the tree the entry would score by without it, and it stays
pending beside that edit. "As the file holds
it" is as JSON reads it: members in any order, `1` is `1.0`, `true` is no
number. A flat test whose entry differs only by its own implicit tree written
out — the tree it already scores by, with no other edit beside it — is as
saved too: there is nothing to save. Below the
tests, Save these / Discard these — the header's Save and Discard, over every
pending edit. Its close button or Esc closes it; under 1024 px it lies over
the header and the strip. An Esc while a menu or a dialog is open closes that
one, and the drawer stays; a second Esc, even while it is still fading out,
closes the drawer.

A Save or an Accept over a `test_spec.json` that was changed on disk since
the tool loaded it — edited by hand, or by `spec update` — is refused in a
dialog: `<path> changed on disk since it was loaded.`, then `Nothing was
saved.` / `Nothing was accepted.`, and Reload, which reads the file again.
With edits pending the button says what it costs, `Reload and discard 2
pending edits`, beside `Keep my edits`; with none it is `Reload` beside
`Close`. A Reload that cannot be done changes nothing: over a deleted file
it says `<path> is gone: nothing was reloaded — put the file back, then
Reload`, and over a file that is no spec that file's own error.

The **Suite page** is a table of the tests, with the dashboard's columns and
words (one row model, D136): Status, Test, Worst NRMSE, Criteria, Sim (s),
Warnings, Edits (a test's staged edits, "2 unsaved") and Message, which takes
the width left over, are shown; the column chooser (the columns icon) shows or
hides the rest — Reference, Resolution, Translate (s), Total (s). Key, Worker
and Elapsed are a live run's and are not offered. One toolbar row above it
holds:

- **count pills** — `PASS n · FAIL n · SIM_FAIL n · NO_REF n · NOT RUN n · EDITED n`, and
  three that come and go: `SCORING n` while rows are not scored yet (a project
  that is opening), and `QUEUED n` / `RUNNING n` while the simulation queue
  holds such a test (each, switched on, stays until you switch it off). A cancelled test counts under NOT RUN. A
  pill filters the table to its status; several pills show any of them, and
  EDITED adds the edited tests. The counts are over every test, whatever the
  filters; a pill with nothing to count is greyed.
- **view icons**, each with a tooltip — group by package / show flat, short /
  full names, choose columns — and the **`.*`** and **`Aa`** toggles, which make
  every column filter a regex and case-sensitive.
- at the right, **"N selected · ✕"** (✕ clears the selection; "(M hidden)"
  when a filter or a closed group hides selected tests — the actions still act
  on all of them), then **Accept** and **Queue** as icons whose tooltip is the
  caption, "Queue 3 (2 already queued)", or the sentence saying what would
  enable it. A click opens the same confirm dialog.

Under the header row, each column has a filter: status by the pills, numbers
by `>1e-4`, `<=3`, `=0` or a range `1..10` (read from the value, not its
display text), text by substring or `*glob*` — the Test column filters on the
full test name, so `*.B` finds `FakeLib.B`. A filter the table cannot read is
outlined red, says why in its tooltip and filters nothing. A click on a header
sorts, within each package while the table is grouped; empty cells sort last
both ways. Grouped, each package is a row of its own that a click, Enter or
Space collapses. The checkbox column selects (its header checkbox selects every
test the filters show); the test's name — or Enter on its Test cell — opens its
page. A name or a message cut at its cell's edge reads whole in the cell's
tooltip.

The table's view — pills, filters, `.*` / `Aa`, grouping, names, sort, columns
and closed groups — is the viewer's: the browser keeps it (per project, by
config file) across reloads and restarts. The selection is the tab's and comes
back when you return from a test. Rows repaint in place as results are scored,
edits staged and queued tests start and end (a queued test's row reads QUEUED,
a running one's RUNNING, a cancelled one's CANCELLED until its queue row is
cleared), keeping the sort, filters and selection. On a cold
library scan the rows the spec and the last run name appear at once, reading
SCORING until the scan and the scoring reach them (QUEUED is a test in the
simulation queue, and nothing else). When the tool's queue has
drained — nothing queued, nothing running — it leaves, like `dstf run`, a
`status.json` listing the tests that ran since it was last written and a static
`dashboard.html` with the tool's verdicts for every test, those of any unsaved
edit included. The dashboard is the report of the last `dstf run` / `compare`
or drained queue; Save, Discard and Reload do not rewrite it, and none of the
three is refused while the queue runs.

Beside the table (above it under 1200 px, and as one line reading "queue
empty" while it holds nothing) is the **simulation queue**: one per tool
process, first in, first out, a test never in it twice. Each row is one test
in run order — its number, its status, its name (a link to its Test page) and
its state in words: `waiting · 3 ahead`, `running · t = 42 / 100 s · worker 2`
(how far its result file has got; `running · 42 s · worker 2` on a backend
whose result is written at the end), `finished · 9 s` with the verdict as its
badge, `failed: <the backend's message>` (one line; the whole message on
hover) or `cancelled`. While a worker is being started the panel lists it
above the rows — `starting worker 1 — loadModel(Modelica) · 85 s`, the step in
the backend's own words — and the next test reads `waiting · worker starting`:
a large library loads for minutes before its first test runs. A worker that
died is brought back for the test it was about to run: `restarting worker 1 —
…`, and that test's row reads `running · worker restarting`. A waiting row
has **Remove**; a running row has **Cancel**, which stops its simulation and
leaves no result (where the backend cannot stop a test — FMPy — the row says
so instead). Until the simulation has ended the row reads `cancelling…` and
the test's strip "Cancelling on worker 2…", with no second Cancel: usually a
moment, but on Dymola a model blocked in an external call does not see the
request and is stopped with its worker after about 12 s; an ended row has **Open test** and the Suite's own actions for
that one test, Accept and Queue. A test queued again keeps the rows of its
earlier runs until Clear finished: such a row says how that run ended
(`finished · 3 s`) and has Open test alone — the verdict's badge, Accept and
Queue are on the test's latest row, since a verdict is the test's as it stands
and Accept stores its latest results. The panel's header counts the rows, holds
the **workers** field — how many tests run at once: 1 when the tool starts,
between 1 and the machine's cores; a change applies to workers
started after it — **Clear finished**, which drops the ended rows and leaves
their results, and a ⋮ with **Stop workers**: the simulator processes end and
their licence seats are free, and the next queued test starts new ones (it is
refused while a test runs). The workers otherwise stay alive while the tool
runs, so a second run of a test does not load its libraries again. The store
writes' lines and the artifacts are listed under the rows.

**A Queue click runs the library as it is on disk.** The workers stay alive,
so each click first looks at the library's files: one stat of every `*.mo` and
`package.order` under `source_path` and under each dependency given as a path,
through a sub-directory that is a symbolic link too
(a Julia project's `*.jl` and `*.toml`; nothing for an FMU or Python project,
whose tests each start a process) — 0.06 s for a 4,000-file library on a local
disk, 1.3 s on a slow mount, and not on the page's own thread. Meanwhile the
tests the click named show as taken: each reads QUEUED in the table and has a
panel row `waiting · checking the library`, with no button yet, and the header
counts it; a second click on one says `<model> is already being queued`. When
the look is done they take their place in the queue. A queued test whose own file changed is
read again first, so an edited `experiment(StopTime=…)` or `x={…}` is what the
run uses and what its row shows; a test whose file is gone, or no longer holds
it, is not queued and the panel says so (`<model>: its file <path> is gone`).
Before a worker runs a test queued after a change it reads the library again:
Dymola reopens it (hundredths of a second on a local disk, 8 to 9.5 s for a
3,000-file library on a slow mount), OpenModelica loads each changed file (a
third of a second) or the whole library — when a file was added or removed,
when a `package.mo` or `package.order` changed, when one file at a time would
cost more than the whole load did (a small library is always loaded whole),
when a changed file sits in a directory with no `package.mo` or one reached
through a symbolic link (its path does not say its class),
and for the click after a reload that failed, since nobody then knows what
the simulator holds — which takes as long as its first load (a second on a
local disk, over a minute and a half for that library on a slow mount), and
Julia restarts its worker. Meanwhile the test's row reads `running · reading
the library` and the panel `worker 1 reading the library —
loadFile("…/package.mo") · 12 s` (`openModel("…/package.mo")` on Dymola). A
library that no longer loads FAILS the test with the simulator's own message
— `MyLib does not load: Examples/Pipe.mo:12: [syntax:error:2] …` on
OpenModelica, the file and the line whether one file or the whole library was
read; on Dymola the test's translation fails ("Translation failed", the
parser's message in the translation log) — and does not run the model the
simulator still held. Not looked at: a dependency given by NAME (`"Modelica"`:
the simulator finds it); `testing.json` itself (`backend_setup` and
`dependencies` are read when the tool opens); an edit made after the click
(the next click sees it); a file rewritten with the same modification time and
size; and a test that did not exist when the tool opened (open the tool again
to list it). A test whose file is gone, unreadable (`cannot be read:
Permission denied`) or no longer holds it is not queued; the panel's note of
it goes when that test is next queued.

The header of every page carries the queue's indicator — `3 running · 5
queued`, or `idle` — and a click on it opens the Suite page at the queue. When
no worker can start (a licence, a missing tool) the waiting tests fail with
the backend's own message, nothing stays queued, and the indicator shows the
message's first words in red until Clear finished or the next test that
starts. **Stop server** asks first while the queue holds a test ("3 tests are
queued or running — stop anyway?"); stopping removes the waiting ones, cancels
the running ones and ends every simulator process.

The **Test page** is one test. A second header row carries the way back to the
Suite, the model id, its verdict, and the backend and baseline it was scored
against. Under it, still in the part that stays at the top, the **status
strip** says the test's situation in one sentence and fills its one next
action: "Fails 2 of 3 criteria. If the new behaviour is right, accept it as
the baseline." (red, Accept filled); "No baseline yet — accept this result as
the first one" (Accept filled); for a failed run, the backend's own reason and
then the next action — "Timed out after 12s (raise it per test with
simulation.timeout in test_spec.json, or globally with --timeout) — fix the
model or its settings, then queue it again." — one line of the message, the
whole of it on hover, and still there after its queue row is cleared. After the
tool is started again it is what is read back from disk: the same line where
the run wrote its reason into its result file (a Python or Julia `simulate`
that raised), else the verdict the run recorded and what its file is — "Run
recorded timed_out — Partial result: …", as `dstf compare` prints it ("The
simulation failed. Fix the model or its settings, then queue it again." where
the run recorded no reason), Queue filled; "No result yet — queue it to simulate it." (Queue filled; the verdict
beside the model id then reads NOT RUN); for a test
the queue holds, "Queued — waiting · 3 ahead" with Remove, and "Running on
worker 2 — t = 42 / 100 s" with Cancel (the running view, below); "Cancelled
at t = 19.2 of 200 s — not scored" for a run that was stopped and left rows,
"Cancelled — no result (its earlier result was replaced when the run
started)." for one that left none and, where no worker could start, the
backend's message (on OpenModelica a library that does not load names the
file and the line: `No OpenModelica worker could start — worker 0: failed
to load library …/package.mo — Parts/K.mo:3: [syntax:error:2] …`), each
with Queue again filled; "2 unsaved edits · verdict would change PASS → FAIL" (amber; Preview
and save opens the preview on this test, and Discard drops this test's edits
only — the header's Discard drops every test's); "Passes against its
baseline." (grey, Accept in the strip's ⋮ menu: there is nothing new to
accept). A criterion on a variable the last run did not record scores "not
found in the result" until a run records it, and the strip says so: with the
edit unsaved, "1 unsaved edit · verdict stays FAIL · 1 new variable: queue the
test to score it", Queue the one filled action (Preview and save and Discard
quiet, Accept in the ⋮); saved without a new run, "1 new variable the last run
did not record: queue the test to score it." (Queue filled, Accept in the ⋮ —
the baseline would
not hold it yet). Where the result FILE already holds the variable — Dymola's
and the JSON driver's (Julia, Python) record every variable — the tool reads it
again the moment the criterion is staged and scores it at once; the strip asks
for a new run only on OpenModelica and FMPy, or for a name the file lacks. The
count is of EDITS — the things you did, not the patch ops they took — and it
is the one count the header ("N pending edits"), Save's and Discard's
captions, the Suite's Edits cell, the Tests drawer and the preview show. A
flat test's first criterion edit also writes its tree out, and reads 1 (the
preview says Save writes the tree); so does one Add over twenty Indices, one
change of a folded row's twenty criteria, one Ctrl+click on a plot, one drag,
one wrap, one move. An edit that lands where the last one did — a value typed
over, a second row clicked into the same table, a second move — replaces it
and stays one, and an edit put back leaves none (see the preview, above).

**While a test runs** its page keeps the plots and the criteria it had — its
earlier result went when the run started, and the verdict beside the model id
reads NOT RUN — and shows the run as it goes. On Dymola and OpenModelica,
whose result file grows during a run, each plot's Actual trace is what the
simulator has written so far, drawn beside the baseline on a time axis that
already spans the whole run, and the strip says how far it has got: "Running
on worker 2 — t = 42 / 100 s" ("starting" until the first row is written,
"t = 42 s" for a test with no stop time). Only the plots that have been
drawn are read, once a second — a plot is drawn when it scrolls into view —
and a zoom, the legend's choices and the scroll position stay as they are
through every update; the zoom starts over when the run starts and when its
result arrives. Every criteria row and the tree's line read
"waiting for result", and nothing on the page shows a verdict until the run
ends; then the page shows the finished result, scored. The Variables drawer
and the add form's picker list the growing file's variables, so a watch plot
added while the test runs draws live. On Python, Julia and FMPy, which write
their result once at the end, the plots stay with the baseline alone, the
strip counts seconds, and one line under it says "live view not available for
this backend". A test that has never had a result is laid out by the
variables its model and its spec name; one whose spec names them only by a
pattern has no plot until its first result.

OpenModelica runs started from the tool write every row to the file as it is
computed (`-mat_sync=1`), which is what makes the trace grow smoothly; on a
slow disk such a run takes longer than the same test under `dstf run`, which
never sets it.

**A run that did not finish** — cancelled, timed out, or failed after it
began writing — shows what it wrote: the Actual trace up to where it stopped,
a dashed marker there ("stopped at t = 5.85 s"), and the strip's "Cancelled at
t = 19.2 of 200 s — not scored", "Timed out at t = …" or "Failed at t = …",
the last two followed by the backend's reason.
Its criteria read "not scored". It is never scored and never a baseline:
Accept is greyed with "a partial result is never a baseline: queue it again",
and the Suite row keeps CANCELLED or SIM_FAIL. The partial trace stays until
the test runs again or the tool is restarted.

Under the strip, the **Statistics card** shows every statistic the run and its
baseline recorded, side by side — the rows the report's Statistics table
shows, under one rule. Closed, as on every page open, it is one line naming
the counts that changed (`1 changed: Nonlinear count 0→3`; a list's count,
total and max are one entry; at most three, then `+N more`), then, after a
`·`, at most two changed times, the one that moved most first (`· CPUtime
+40%`); `all match` when no count and no time changed; `no count changed`
leads a line of times; or `no run result`, `no baseline statistics`,
`nothing recorded`. A statistic has **changed** when the criterion its `+` would add
would fail: a count on any difference, a time beyond 10%. Open, every count
and time is a row under Translation / Simulation / Timing, with its run and
baseline values (a time to 3 significant figures, `0.800 s`; the report's
table prints it in full, `0.8 s`) and the
difference; a changed row is tinted. Lists and names are left out: nothing
scores them. A row no criterion reads ends in a `+` — "Add a requirement:
exact match" for a count, "…within 10%, as a warning" for a time — that adds
a `scalar` criterion on it at the end of the tree (a time inside a `warn`
where it lands in an `and`) and opens its editor under the row. A row a
criterion reads shows that criterion's pill and its requirement (`exact`,
`±10%`, `in [a, b]`; `−5% / +10%` where its tolerance's two sides differ)
instead; click the row to edit it there — the tolerance's two fields, the
tolerance mode, bounds; there is no plot, so the fields are the only way to
its sides. A criterion reading a quantity no
statistic holds gets a row under "Other scored quantities". The `+` is grey,
with the reason as its tooltip, on a
simulate-only test, where nothing is scored yet, where the last run no
longer holds what it scored (queue it again), where the test's flat criteria
cannot become a tree without changing its scores (write a `metrics` tree
in `test_spec.json`), and under a `warn` or `weighted` root.

Below the card, the criteria tree is
closed to a single line — the root's verdict, what the root is, the tally, and
the first failures by name, each a button that jumps to its criterion — and
"Show tree" opens it to a row per node (see "The open tree", below). Then come
the test's plots — derived
from its criteria, one per scored variable or family in the tree's order, then
its watch plots (see "Plot layout"); a line above them says which ("3 plots ·
derived from the criteria · 1 watch", "one per tracked variable or array"
with no tree, or "2 watch plots · no criteria on a signal yet" with none
derived) — each drawn as it scrolls into view. A plot's title is its
variable's label, with the result-file name muted beside it where they differ.
Under each plot are its variable's criteria rows, in TREE order, each led by
its ancestors' kinds (`and › k-of-n ›`): a swatch, a verdict pill,
`metric · <the variable's label>`, the CLI's criterion line, and that node's
controls (Edit tree, below). Clicking a row opens that criterion's editor
below it, one at a time, with the plot above as its editing surface — drag a
ring, a tolerance arrow or a bound line, or edit the fields (see Edit on
plot); every change is staged, and Save writes them. The criteria rows' header carries one `+` ("Add a criterion for
x", x the plot's variable, a family's first member; its form's picker offers
every variable of the result — see Edit tree). Context series are listed in a row above the
plot, each with a `×` that takes it off. A derived plot has no Remove — an
icon says "This plot exists because of its criteria." — and goes when its
last criterion goes; a watch plot reads "No criteria yet." and has **Remove
plot**; its `+` adds a criterion on its variable, which tracks it. Reset layout, on
the tree's line, stages removing the `plots` key: the context series and
watch plots go, the derived plots stay. Focus on the plot's own toolbar shows
that plot alone — the focus bar reads "Showing 1 of N plots", and its select
names the plot and switches to any other (a watch plot marked "watch") —
until "Show all N plots". A long variable label is cut
in its MIDDLE — its head, an ellipsis, its index (`cat(1, pipe2.mediums.T, …[3]`),
or its last 12 characters when it has no index that near its end — with the
whole label as its tooltip; a model id is cut at its start.

An array to try what follows on ships with the tool: the Modelica example's
`ModelicaTestingLib.Examples.HeatRodTest` is a rod of twenty nodes whose
temperatures are one array, `T[1]` … `T[20]`. Its `test_spec.json` entry tracks
`T[*]` and scores it with twenty identical `tube` criteria — what Indices
`1:20` writes — and one `scalar` on `T[10]` alone, so its Test page shows the
array's plot with one folded row, `tube · T[1:20]` (`20 pass`), and the
`scalar · T[10]` row after it.

Under an array family's plot, the elements whose criterion is one and the
same — children of one node, with the same metric, parameters, window and
baseline — are ONE folded row: a chevron, the worst of their verdicts as its
pill, `metric · <the family with its indices>` (`nrmse · pipe.T[1:20]`) and a
count by verdict (`18 pass · 2 fail`, with an advisory and an unscored count
when either is non-zero). The chevron unfolds it to its members, each the
criterion's own row indented under it, with its own swatch, editor, handles
and node controls; which rows are unfolded is the browser tab's. Clicking the
folded row opens its editor: one member's fields, showing the value they
share, and every change is applied to ALL of them as one edit (on a test with
no saved tree its tree is written out under the edit, which the preview says;
the count reads 1 all the same). It draws no handles — "These fields change all 20
criteria. Unfold to drag one on the plot." Its **Indices** field holds the
elements the criterion is on, in the add form's syntax (see Edit tree), and
beside it what Enter would do, as it is typed: `→ 5 elements (removes 15)`,
`(adds 2)`, `(no change)`. Enter or Apply removes the criterion of each
element left out and adds one, with the same parameters, for each new one, in
index order among the fold's; Esc puts back the text applied last. A change
that would remove more than half of the fold's criteria asks once ("Removes
19 of 20 criteria — Apply again to confirm"). An index the last result does
not have is refused ("pipe.T[1:20] has 20 elements; 21 is not one of them."),
so is an element that already has a criterion of that metric of its own
there ("pipe.T[3] already has its own nrmse criterion under this group — edit
or remove that one, or leave 3 out"), and an empty field says to unfold the
row and remove its criteria instead. A member whose criterion is edited on its
own stands as its own row from then on — shown after the fold's members,
flush with the fold's row — and the fold's label shows the gap
(`pipe.T[1,2,4:20]`); edited back, it folds again. While a fold's editor is
open its members stay that fold whatever is typed; Done shows the rows as the
tree has them. Two groups of one family are two folded rows, and a `UnitTests`
element never folds. None of this is stored: `test_spec.json` holds one
literal leaf per element, the tree card shows one line per leaf, and the
report lists every leaf. The preview says what each of these gestures did
above the entry's diff (`tolerance → 0.05 on 20 criteria of pipe.T[1:20]`,
`Indices: removed pipe.T[20]`).

A plot's legend holds its signals only — actual, reference, any overlays and
its context series —
on a line above the plot area, so it never sits on the trace or on a ring.
Each criterion's drawing is shown or hidden by the swatch on its row, filled
while it draws and an outline while it does not; Enter or Space on a focused
swatch does the same. Before a swatch is touched, what draws is the metric's
default — tube, points, event-timing and dominant-frequency draw; range and
scalar, whose bounds cross the whole plot, do not — except that a failing
criterion draws. The open criterion always draws, since the plot is its
editing surface, and its swatch is locked while its editor is open. A failing
criterion's swatch hides it like any other: the failure still shows on the
row's pill, the card's, the closed tree line, the header and the Suite page.
The error series are one Error select per plot whose subject is one variable, on the
plot's toolbar — No error, or one of the signed, absolute and per-point NRMSE
series, each on its labelled axis.
Plotly's own toolbar stands vertically in a gutter right of the plot, clear of
that axis's labels.

**The rail's drawers.** A rail down the Test page's left edge opens one drawer
at a time beside the plots, narrowing them: **Tests**, the suite's tests with
this one highlighted (a click opens that test, and the drawer stays open), and
**Vars**, every variable of the result — a check adds a row's variables to the
selected plot as context series or takes them off (the plot's own variable is
checked and grey), "+ new plot" makes a watch plot of one variable after the
others (grey on a plot's variable, and on a family: open it and pick one).
A run that failed has no plots: the drawer says "The run failed: no plots",
and its checks and every "+ new plot" are grey. A run that succeeded and whose
variables the test tracks none of reads "no plots: this test tracks none of
the result's variables" on the count line ("+ new plot" makes a watch plot of
one); "no plots: the result has no variables" is for a result that holds none. A test with no result — never run, or
a run that never finished it (stopped before it) — is NOT RUN, not a failed
run: the drawer says "No plot yet: + new plot adds one", as for any test with
no plot, while the strip, the count line and the tree line say the test has no
result.
Rows fold as the plots do: an array by result-file name into one row, a
`UnitTests` element always a row of its own. A row's label has its first line
to itself; its result-file name (where the names menu shows it), "plot N" and
"+ new plot" are on a smaller second line, which a row with nothing else on it
drops ("+ new plot" then sits at the first line's right). The open drawer
resizes by its right edge: drag it, or focus it (Tab) and press ← / → (16 px,
64 with Shift), Home or End. It is 320 px at first, at least 240, and at most
60% of the window and never so wide that the plots have less than 480 px; the
width is the tab's, so a switch of test keeps it, and a narrower window
narrows it to fit. Both drawers carry one control set, and every setting is
the tab's, so a switch of test keeps it:

- a filter box with **`.*`** (regular expression) and **`Aa`** (match case)
  beside it — a substring, or a glob with `*` / `?` matched against the whole
  text; the Vars filter asks a variable's name and its label. A pattern that
  is not a regular expression, or takes too long, is said under the box.
- icon buttons opening menus, each choice checked when current: the arrow keys
  move among the choices, Home and End jump, Enter chooses, Esc or Tab closes.
  Tests: **Sort** — package order, name A→Z, or status, failures first (FAIL,
  SIM_FAIL, scoring failed, NO_REF, PASS, NOT RUN, then unscored); a **group** toggle
  (by package, or flat); **Names** — short (cut at its end where it does not
  fit) or full (cut at its start, so ids sharing a package read apart; the
  whole id is the row's tooltip). Vars: **Sort** —
  result-file order or A→Z by what a row reads; **Group** — by component,
  flat, or tracked first (the tracked elements of an array and the rest of it
  fold apart: never one row over both); **Names** — the expression, the
  result-file name, or both (the expression with the name muted beside it;
  under Expression, a label another variable of the test shares, or another
  row of the drawer reads, still keeps its name beside it). An opened family
  stays open when the group changes or the filter keeps part of it.
- on Tests, **count pills** — `PASS n · FAIL n · SIM_FAIL n · NO_REF n · NOT RUN
  n · EDITED n`, over every test whatever the filter, filtering as the Suite table's do; a
  test whose scoring failed counts under SIM_FAIL, as its Suite row reads.

**Edit criteria** is the top of the open criterion's editor: the metric, the
form derived from that metric's config dataclass, the window, and the baseline
the leaf scores `against` — `primary`, plus the soft_checks when the leaf sits
under a `warn`, since a soft_check anywhere else is what the validator
refuses. A criterion without a tolerance of its own is scored with the test's
base one, and its Tolerance field says which on the line under it whenever the
field is empty — "in effect: 0.01 (the test's tolerance)", or the baseline's,
or the project's (`testing.json`'s, the CLI's, or the 1e-4 default) — and
never writes it, so an unset tolerance keeps falling back; a value in the
field, typed or the criterion's own, takes the line away, and clearing it
brings the line back, naming the base the criterion would then be scored
with. A field with nothing in effect shows its default as a placeholder,
never written either. Every field of the row keeps that
line, so their input lines sit level. Structured fields — tube control points,
checkpoints, declared events and peaks — are shown read-only here and edited in
Edit on plot. Every control change stages one `replace` of the whole leaf and
rescores the test, so the figure and the verdicts follow the edit; consecutive
replaces of one leaf collapse into one pending op, so the exported patch
carries one op per leaf rather than a keystroke log, and the pending count one
edit (the preview
shows the entry as it will be saved either way), and an edit that puts the
leaf back as the file holds it leaves no pending op at all.

A tolerance with two sides — every one but `nrmse`'s (see "Tolerances with
two sides") — is two number fields with a link toggle between them: `−
Tolerance` and `+ Tolerance` on a scalar, `− Default tolerance` / `+ Default
tolerance` on points, `early` / `late` on an event-timing criterion's time
tolerance, `− Tube abs` / `+ Tube abs` on a tube. The toggle's state is the
shape `test_spec.json` holds, and the tool keeps no other record of it:

- **linked** (the `link` icon, in the accent colour; how an unset tolerance
  and a number open): both fields show the one number, typing in either sets
  both, and the file holds a number. Clearing either clears both, and the
  tolerance is unset.
- **unlinked** (`link_off`, muted; how a pair opens, equal sides or not):
  each field is its own side and the file holds `{"lower": …, "upper": …}`.
  While one side is blank nothing is staged, the blank field says
  "required", and the leaf holds the pair it held when that field was
  entered — never a zero passed through on the way to empty. Both blank is
  unset, and linked again.
- **the toggle**: unlinking writes the number twice; linking takes the
  LARGER side for both, so linking never narrows what the criterion allows.
  Tab reaches it, Space flips it, and its name says the state ("Linked: both
  sides of Tolerance change together").

The in-effect line shows under the first field while both are empty. Under a
plot, a half-typed pair stays as typed, blank and marked, when the editor is
rebuilt by a drag on the plot or an edit in a table beside it.

Every number field of the tool keeps what you type until you leave it: a
stage rescores the test, which takes longer than the gap between two keys,
and the answer to one key no longer lands under the keys after it. The mouse
wheel over a focused number field scrolls the page and steps nothing; the
arrow keys still step it.

A value that can never score is refused where it is typed, before anything is
staged: a negative tolerance — a number or either side of a pair, "tolerance
must not be negative; got −0.5", "time_tolerance (early side) must not be
negative; got −0.5" — a tube's `tube_abs` / `tube_rel` whose two sides sum
below zero, which puts the tube's upper bound under its lower one: a negative
number ("tube_abs must not be negative; got −0.3") or a pair ("tube_abs sides
must not sum below zero; got lower −0.1, upper −0.5"; a pair with one
negative side and a sum above zero is a band shifted off its reference, and
is taken, and so is one whose sides sum to exactly zero), a lower bound above
the upper one ("min_value 9 exceeds max_value 2"), and a scalar's two bounds
equal. A tube's width is refused only while its Min width is empty, zero or
below: a positive Min width widens both sides to itself, so no width over it
leaves nothing inside, and a width that sums below zero is taken there — as
is a Min width typed under such a width written by hand. Clearing the Min
width from under one is then what is refused, with the width's sentence. A
tube's Min width below zero is refused with them
("tube_min_width must not be negative; got −0.3"): it scores, but as a floor
under no width it says nothing. Zero is a tolerance, and a typed `-0` is
staged as 0. The field keeps
what was typed and the editor says the sentence; the leaf goes back to what
it held when the field was entered, so a value passed through on the way
(`-0` on the way to `-0.5`) is not left staged. This is the editor's rule
alone: a `test_spec.json` written by hand with such a value is read and
scored as it always was.

**Edit tree** is the controls on every node of the tree and on every criteria
row — `+`, wrap and `−` — and the `+` in each plot's criteria header. Each
stages one op per structural change: wrap a node in a combinator, change a
combinator's kind and its `k` / weights / threshold, add a leaf, remove a node
(the root has nothing to be removed from). `+` on a criterion adds the new one
right after it; on a combinator, as its last child; a plot's `+` adds under the
root. A test the flat path scored gets its implicit tree materialized by the
same op.

The add form's **Variable** is a picker over every variable of the result
file, tracked or not, in sections: *On this plot* (the plot's variable and its
context), *Other variables*, and *Diagnostics* (what the run recorded as one,
such as Dymola's `CPUtime` and `EventCounter`). Its box filters as the drawers'
do — a substring, a glob, `.*`, `Aa` — on a row's label or its result-file
name; at most 200 rows are listed ("Showing 200 of N — narrow the search").
↑ and ↓ move a ring through the rows from the box, Enter chooses the ringed
row, a click chooses a row and moves the ring to it; the line under the list, "Chosen: …", names the
choice even when the filter hides it. The **Metric** select follows the
choice: a diagnostic offers `scalar` only, anything else every metric that
scores a time series (and the one chosen before a diagnostic comes back).
When the variable chosen is an element of an array the result holds more of
(`pipe.T[12]` of `pipe.T[1]` … `pipe.T[20]`), an **Indices** field shows under
the picker, filled with every element (`1:20`) — or, when the form was opened
from the `+` on one element's own row, with that element's index (`3`). It
takes an index, a range with its end included (`1:5`), a stepped range written
start:step:end as Modelica writes it (`1:5:20` is 1, 6, 11, 16) and a comma
list of those (`1:3,7,10:2:20`), the indices as the result file names them (a
zero-based array has a `0`); an index the family lacks is refused. Under the
field a count of what it names, as it is typed (`20 elements`), and Add reads
the same (`Add 20 criteria`) — `1:20:5` is ONE element; the form's title says
what is being added to (`Add a criterion for pipe.T[1:5:16]`). Add — or Enter
in the field — writes one criterion per chosen element where a single one
would have landed, all with the same parameters (seeded once, from the first
chosen element), as ONE edit — the whole tree, as a move stages; `3` alone
adds the one. Under the family's plot they read as one folded row (above). A
family behind a multi-dimensional index (`port[2, 1].C[3]`) is indexed by its
last index; a `UnitTests` element gets no field.
A criterion on a variable the test did not track is what tracks it: every
backend records, stores and scores a criterion's variables after the test's
`UnitTests` variables and `variables` patterns, with no change to `variables`
(see the strip, above, for the new run). A diagnostic's criterion scores its
stored final — `scalar` with `quantity: "<name>.final"` — exactly for a count
(`EventCounter`, or a diagnostic of whole numbers) and within 10% otherwise;
a time (`CPUtime`, or a diagnostic of fractional numbers) is added inside a
`warn` where it lands in an `and` (beside a root criterion too, which makes
the root one), since a run's CPU time moves with the machine's load; under
an `or` or a `k-of-n` it is added bare, because a warn there would pass its
parent. Such a criterion has no plot: after its Add its editor opens under
its row of the Statistics card, where it is edited from then on. On a
simulate-only test the form says "simulate-only: nothing is scored" and
its Add is grey.

A combinator's kind is a dropdown in its row — and, or, k-of-n, warn,
weighted, and below a separator "unwrap — its children take its place" (left
out on a root with several children, which cannot unwrap). And, or and warn
stage at once; k-of-n and weighted open the node's form preset to that kind,
since they need `k` or a threshold first. A click on a k-of-n's or a weighted's
row — the root's too — opens that form, and a second click closes it; and, or
and warn have no fields, and a click on their row does nothing.

Each form opens as a row under the node's row, titled for the node, and it is
the one thing open on the page: it replaces an open criterion's editor, and an
editor or another form replaces it; clicking a criteria row closes a form open
under it and opens nothing. A row that opens below the fold scrolls just far
enough to show itself. It shows the chosen combinator kind's fields and no
other kind's, one sentence saying what the action does to the tree, and Cancel
beside the action. It is built from the tree as it is when it opens, and
rebuilt when an edit staged elsewhere moves its node. An action that goes
through stages its op and closes the row — after an Add, the new criterion's
editor opens under its plot; Edit stages only what changed, so an unchanged
Apply stages nothing. A change the validator refuses is said under the node's
row, stages nothing, and leaves the form open with what was typed. A weighted
form's threshold starts empty, and its action is grey until one is typed; its
weights start as one `1` per child. Unwrap gives a single-child combinator's
place to its child, and a combinator with several children gives its place to
all of them, in order — which the root, having no parent, cannot do. `+` is
grey under a `warn` (it takes exactly one child) and under a `weighted` node
(one weight per child), with that reason as its tooltip.

**The open tree.** Each row shows where it sits: a 6 px bar per level at its
left — level 1 blue, 2 violet, 3 cyan, 4 slate, then again — in place of an
indent, and the root row, which has no bar, ends "· root". Hovering a group's
row deepens its level's bar on it and every row under it; hovering a
criterion's row does the same for its group. Every group but the root has a
chevron that folds it: its rows hide, and its row says what it hides — "· 3
hidden · 2 pass · 1 fail", every criterion under it counted once by verdict.
Expand all and Collapse all are the two icons beside the tally (Collapse all
leaves the root open). The folds are the browser tab's, per test: they stay
when you switch tests and come back, and follow their groups through every
edit you make in that tab; any other change to the test's tree — a Discard, a
Save that reloads, an edit in another tab — opens every group, whichever page
the tab is showing when it happens, and one made while the tab had no page
open opens every group of every test when a page of the tab opens. Only the tree
opens a folded group by itself: a form or an editor opened under a row, a
refusal said under one, or a move's line opens the groups around it; while
dragging, resting on a folded group's row for 0.7 s opens it. Clicking a
criterion's row opens its editor under it — the page's one editor, so one
open on a plot closes, and the plot and its row follow what you type — with
**Edit on its plot**, where its plot can drag its handles, to move the editor
there. The ⤓ on a criterion's row scrolls to it on its plot (or on the
Statistics card) and flashes it, opening nothing. A row whose click opens
something is also a button to the keyboard: Tab to it, Enter or Space.

**Moving a criterion or group.** In the open tree, every row but the root's
has a ⋮⋮ grip before its pill and a **Move to…** icon among its controls; both
move that criterion, or a combinator with everything under it, to another
place in the same test's tree. Drag it by the grip: over a row's top half the
drop puts it BEFORE that row (an accent line from where its content starts,
right of its bars), over its
bottom half ONTO the row (an outline) — a combinator takes it as its last
child, a criterion is wrapped with it in an `and`, the criterion first. The
root's row is all "onto", and there is no "after": to put a node last at an
outer level, drop it onto that outer combinator. A place the move refuses
outlines its row in red while dragged over, the tree card's line says why, and a
drop there does nothing. Move to… opens a menu of every place in tree order —
each combinator's "before" each of its rows (`before range · x`) and "at the
end", each criterion's "wrap both in and" (two siblings that read the same have
their places numbered, "at 2"); the place the node already is reads "where it
is", and it and every refused place are greyed, a refused one with its reason
under it. The arrow keys move among the places, Enter chooses. A drag that
started on rows rebuilt before its drop (an edit staged meanwhile) is refused:
"The tree changed while you dragged: try again."

A move keeps the tree valid by changing what it must: a combinator it empties
is removed, and its parent if that empties too; a k-of-n left with fewer
criteria than its `k` lowers `k`; a node moved into a weighted gets weight 1,
and one moved out takes its weight with it. Afterwards any open editor or form
closes, the moved row is scrolled into view and flashed, and a line under it
says what changed — "Moved. k 3 → 2.", "Moved. The empty warn was removed.",
"Moved. Weight 1 given.", "Moved. Weight 2 went with it." — until the next
form, editor or edit. Each refusal is one sentence: the root, which holds the
whole tree; a group into itself; into a `warn`, which holds exactly one
criterion or group (drop onto that one to wrap both in `and`); a move that
puts under an `or` or a `k-of-n` a child that can never fail — a `warn`, or a
group that passes whatever its criteria score — which would pass its parent
with nothing else passing (a tree that already has one is left alone; only
the move is refused); and whatever the validator refuses, such as no primary
criterion left outside a `warn`. A move is ONE pending edit — the whole tree,
staged as one `add` of `metrics` — and a second move right after it replaces
it, so the pending count stays 1. On a test the flat path scored, the preview
shows the whole `metrics` block added: the test gains its first tree. Moving
is the tree's alone: a plot's criteria rows have no grip and no Move to…, and
nothing moves between tests.

**Accept** applies to the selected tests: their current results become
baselines through the same accept function `run --accept` and `compare --accept`
call, so the tool cannot accept a result the CLI would refuse. The panel lists
one row per selected test — `model · status · what accepting will do` — and the
button counts the rows that will be stored. The one refusal a row cannot
forecast is a trajectory carrying `NaN`/`Inf`: the store reads the values only
as it writes them, so that test is declined during the write and the footer
line reports it with its reason. A row reads `will store`, or
the reason it will not: `no simulation result`, `simulation failed`,
`re-run before accepting` (the last run still records that test as pending, so
its result may be whatever the simulator had flushed when the run was aborted),
`simulate-only: no baseline` (a simulate-only test is neither stored nor
skipped — it has nothing to compare) or `no tracked variables in result`. The
button itself is greyed when no row will be stored, carrying that first row's
own reason — and with nothing checked it reads `Accept needs a selection`. The
write reports one line in the queue panel — how
many baselines it stored, where, every test it skipped with its reason, and
the simulate-only tests of the selection (`Simulate-only, nothing to store:
…`) —
and the results reload, so a re-baselined test's verdict flips in place.
Accept is refused for a test the queue holds — "… is queued: accept it when
its run has ended" — since the result in memory is about to be replaced, and
for a test whose last run did not finish: what its page draws of that run is
a partial result, never a baseline.

**Queue** appends the selected tests to the simulation queue (above), in
project order; a test the queue already holds is left out, and the caption
counts both — "Queue 3 (2 already queued)". The panel names the backend the
runs will use — `runs through Dymola` — and lists the tests. The queue runs
on a backend of its own, the same choice a CLI run makes by default (the
persistent workers where the backend declares them), and narrates nothing to
the console `dstf author` was started from — a logged warning or a
simulator's own output is not narration and still appears there. A backend
that can only run whole batches, or cannot be built on this machine, refuses
the first Queue with its own sentence, and the button carries it from then on.

A queued or running test is not locked. A queued test takes every edit and
runs with the spec as it stands when it STARTS, unsaved edits included — the
caption says so ("with 1 unsaved edit, as they stand when each starts") — so
a criterion added on a new variable while it waits is recorded by that run. A
running test's criteria can still be edited and are scored, as they stand
then, when its result arrives; its simulation settings are locked until its
run ends. As each test ends its result alone is read back and that test alone
rescored. A test that fails keeps the backend's message on its queue row; a
cancelled one has no result.

**Edit on plot** is the rest of the open criterion's editor, scoped to that one
criterion: it is available when that leaf's metric has handles or a table — a
`tube`, `range`, `scalar`, `points`, `event-timing` or `dominant-frequency`
leaf. (An `nrmse` criterion says `nrmse has no on-plot handles` in the editor's
place.) On a test whose flat criteria cannot become a tree without changing
its scores, the editor draws no field and says that reason on one line, in
place of both Edit criteria and Edit on plot; a reason both would give
appears once. The plot above the editor
carries rings and arrows for the open criterion alone, and nothing else on it
takes the pointer: a press on a checkpoint's tolerance box, a window, a result
line or another criterion's bound line is the plot's own (a box zoom). What
drags:

- a green ring on each tube control point (upper and lower edge), each
  checkpoint and each declared peak (on the spectrum panel) — press it
  anywhere on the ring;
- an orange arrow just outside each side of a tolerance: above and below a
  checkpoint's box, and before and after it where the row holds a
  `time_tolerance`; above and below the final-value box of a `points`
  criterion with no rows; at the two edges of an event's window and of a
  peak's window; above and below a tube of constant width, at the band's
  middle sample;
- the horizontal line of a `range` or `scalar` bound, and the two lines of
  what a `scalar` scored against its baseline allows (the baseline's scalar
  plus `upper`, minus `lower`) — drag it up or down. A line zoomed or panned
  off the plot takes no pointer: bring it back into view, or use its field.
  A scalar on a statistic has no plot: its two fields are its sides.

**When the arrows show.** At rest none does, so they do not clutter the plot.
Each checkpoint, event or peak has a hover area — the box around its ring and
its arrows' anchors, 25 px larger on every side; a window's, its edges over
the whole height of its panel — and its arrows show while the pointer is in
that area, and for the whole of a drag. A scroll is read as a move: a
checkpoint wheeled out from under a pointer at rest hides its arrows, and one
wheeled under it shows them, wherever on the page the pointer was resting —
on that plot or not. With the pointer at rest on a checkpoint, a turn of the
wheel that takes the whole plot 300 px from under it hides the arrows, and
the turn back shows them again with no move of the mouse; a checkpoint
wheeled 300 px to a pointer resting above its plot shows them too.
Points whose areas overlap, or just touch, show their arrows together, as one
cluster: with their nearest anchors 50 px apart two points are one cluster,
51 px apart two. Two arrows of a cluster that would be drawn over each other
are staggered: each sits one step, 9 px, to the side of its place — a time or
a frequency arrow above or below it, a value arrow left or right of it — and
a third on the same spot three steps. A staggered arrow still sets its own
edge, and a drag moves that edge as far as the pointer goes. An arrow's
tooltip says what it sets ("Drag to set
the upper tolerance", "Drag to set how much earlier"), and the cursor over it
the way it moves — up and down for a value's side, left and right for a
time's or a frequency's. The plot's own hover label gives way within 20 px of
a shown arrow or its ring, and reads the curve everywhere else. On a touch
screen, where nothing hovers, the open criterion's arrows are always shown.
An arrow whose edge is off the plot area (zoomed or panned away, by more than
half a pixel) is not drawn at all — neither out in the margin nor inside the
plot — and adds nothing to its point's hover area; one whose edge lies on the
plot's own edge is drawn whole in the margin, over the tick labels, and takes
a press there. A ring counts as on the plot while any of it is drawn (its
centre within its 7 px radius of the plot area): hovering what is left of it
shows the arrows whose own edges are on the plot. An arrow of a peak's window
that would fall outside the spectrum's own range — a lower one next to
0 Hz — is drawn on the inside of its edge instead.

**The hint line** at the top of the editor says what this criterion has to
drag, read off what its plot draws: "Drag a ring or a tolerance arrow on the
plot (hover a point to show its arrows), or edit the fields." (checkpoints,
declared peaks); "Drag a tolerance arrow on the plot (hover near it to show
the arrows), or edit the fields." (a tube of constant width, an event's
window); "Drag a ring on the plot, or edit the fields." (a tube's control
points); "Drag a line on the plot, or edit the fields." (a `range`, a
`scalar`); "Edit the fields." (`nrmse`) — then, for a criterion with rows a
click adds to, "Ctrl+click adds a point." (a checkpoint; "an event", "a
peak", "a control point" for the others) — each followed by "Edits are staged
as you make them; Save writes them." While a field is armed (below) the line
says only what the next click does: "Click the plot to set row 1 —
Esc cancels."

**Ctrl+click adds a row that settles on the data.** With a criterion open,
Ctrl+click on its plot (⌘+click on macOS) adds one row to its table — one
pending edit — placed by what it clicks ON, not by the pixel:

- a **checkpoint** takes the clicked time and no value of its own, so it
  follows the baseline there and its ring stands on the reference curve.
  Where the test has no baseline to follow, it takes the run's value at that
  time as its `value`;
- a **declared event** takes the time of the run's event nearest the click;
- a **declared peak** — a click on the spectrum panel — takes the frequency
  of the nearest peak of the run's spectrum;
- a **tube control point** takes the clicked time and the tube's own widths
  at that time, so adding it changes nothing until you drag it.

Every other field of the new row is what **Add row** gives. A criterion with
no rows — `nrmse`, `range`, `scalar`, a tube of constant width — takes no
Ctrl+click. A click that names no place for a row says why under the plot:
the run has no event, a peak is added on the spectrum — or a row already
stands there ("a checkpoint already stands at t=0.6 (row 2)"): within one
snap step of it, or, for a peak, inside a declared peak's own window. A
double-click still resets the axes, a Ctrl+drag is still Plotly's own, and a
press on a ring, an arrow or a bound is a drag: none of them adds anything.
Rows are removed in the table.

**Arm it, then click the plot.** What a click on the plot can place has a
small ⌖: each row of a table has ONE, in its first cell, and a criterion's
own field has one where the plot draws it — a bound, a side of a tolerance
some row is held to, a window's start and end. (A mode, `against`, a tube's
minimum width, `nrmse`'s tolerance, the width of the tube mode not in use,
a scalar's bounds while it is scored by its tolerance: none.) Press ⌖ and
it is **armed** — its ⌖ is filled, the hint says "Click the plot to set row
2 — Esc cancels.", and the plot itself says so too, with an accent outline
and the same sentence under it — and the next plain click on its plot sets
it and disarms. One is armed at a time. Esc, pressing ⌖ again, closing the
editor, opening another criterion, adding or removing a row, changing the
mode, and any edit of the test — Discard and Save too — disarm. The ⌖ is
the mouse's — the gesture ends in a click on the plot — and Tab passes it
by.

A click is a drag released at the click, so it is snapped, clamped and
refused exactly as the drag is — a refused click says why under the plot
and leaves the field armed.

- A **row** is placed as its ring dragged to the click: its time (a peak's
  frequency) from the click's x, and its value from the click's y where
  the row holds a value of its own — a checkpoint that follows the
  baseline goes on following it. A declared event's time is the click's x.
  A row's tolerance sides are set by their arrows, or typed.
- A **tolerance side** of the criterion: its EDGE lands on the click — the
  side becomes the distance from its target to where you clicked, measured
  from the row nearest the click among those that defer to it.
- A **bound**: the click's y — through its line, or directly where the
  bound is still blank.
- A **window's** start or end: the click's x.

Each belongs to one panel. A click on the other — the spectrum, with a
checkpoint's row armed — is refused ("row 1 is set on the time plot") and
leaves it armed.

**The fields follow a held handle.** While a ring, an arrow or a bound line
is held and moved, the fields it will change show the value its release
would write — a number tolerance in both its fields, a pair's side in that
one — and nothing is staged: the pending count does not move until you let
go. A drag released where it is refused, or not moved at all, puts the
fields back. The box or band a handle belongs to follows on release, as
before. A field you type in during a drag is yours: no later preview shows
over it.

**Placed values are snapped.** A value the pointer places — a drag's
release, a click, and the preview of either — is rounded to a readable
step: the power of ten nearest to a thousandth of what its axis SHOWS. On a
time axis from 0 to 1 that is 0.001; zoom in to a tenth of it and the step
is 0.0001. A coordinate (a time, a value, a bound, a frequency) and a
tolerance side in the axis' units are rounded to a multiple of the step; a
relative side — a fraction of its target — to 3 significant digits. A value
is snapped first and clamped or refused after. What you TYPE is never
snapped, and neither is what a row settles on (an event's time, a peak's
frequency, the tube's widths): those are read off the data.

Releasing a drag stages one `replace` of the leaf the handle belongs to and
rescores the test; the plot redraws in place, so your zoom stays. Consecutive
drags of one leaf collapse into one pending op. What a drag means:

- A tube handle's vertical position sets that edge's width at the point's time
  (the value itself for `abs`, the distance from the reference for `band`, that
  distance over |reference| for `rel`); moving it sideways moves the control
  point's time.
- A checkpoint with an explicit `value` moves in both time and value. One that
  follows the baseline (no `value`) moves in time only, so a drag never turns it
  into a fixed-value check: dragged straight up or down it stages nothing, and
  a drag that also goes 8 px or more sideways is a time move and stages the
  new time.
- A peak moves in frequency only; a bound moves in value only.
- A tolerance arrow moves along ONE axis and sets its side to the distance
  from the target to where it was let go: for a checkpoint, the height over
  (`upper`) or under (`lower`) its target — over the target's magnitude in a
  `rel` row — and the time before or after its own; for an event, the time
  early or late; for a peak, the distance from its frequency, as a fraction
  of it in `rel`; for a tube of constant width, the distance from the
  reference at the arrow's own time; for a scalar's line, from the
  baseline's scalar. Where the spec holds a number (linked) the drag writes
  a number, and both sides move; where it holds a pair, that side alone.
- The side is written where its row gets its tolerance: the row's own
  `tolerance` / `time_tolerance` where it has one, else the criterion's —
  so every row that defers to the criterion's moves with it. (Where the
  deferring rows differ in `tolerance_mode`, the dragged row takes a
  tolerance of its own, copied from the criterion's, and the others keep
  reading the criterion's.) A peak without a `tolerance` gets one on its
  first drag. Auto-detected events share the criterion's `time_tolerance`,
  so every window moves.
- An axis the pointer travelled less than 8 px along, press to release, is
  left exactly as it was: a ring dragged sideways keeps its value to the
  last digit, an arrow dragged across its axis moves nothing, and under 8 px
  both ways the shape goes back and nothing is staged.
- A shape let go outside the plot area lands on the plot's edge: its
  position is held to the range the axes showed when it was pressed.
- A time dragged past where the handle can be drawn lands on that edge, and
  the rest of the drag still applies. For a tube point or a baseline-following
  checkpoint that is the first or last reference sample inside the leaf's
  window; for a checkpoint with a `value`, the window's own start or end.
  A checkpoint of either kind is also held inside the run's own first and
  last sample in that window, where the two spans overlap: past the run it
  would read "not scored".
- A number a drag writes into the leaf is snapped (above) and never holds
  more than 12 significant digits, so the spec file reads `0.3`, not
  `0.30000000000000004`. An axis the drag did not move is not re-snapped: a
  hand-written `0.123456` stays until that axis is really dragged.

After every drag the plot is redrawn from the staged spec, so anything the drag
did not change goes back where it was. A drag the leaf cannot take snaps back
too, and its reason is said twice: in the editor, and on a line directly under
the plot, above its criteria rows, where the hand is — the editor can be a
screen below. The next edit of that criterion that goes through, a drag or a
field, takes the line away; so does closing the criterion. (A refused FIELD
edit is said in the editor only, where the typing is.) What is refused: an
upper edge dragged below the lower one, a negative width, a `rel` width where
the reference is zero, a control point moved onto another's time, a lower
bound above the upper one (a `scalar` leaf also refuses equal bounds, which
`range` accepts), a peak at zero or negative frequency, a `band` or `rel`
width or any time on a leaf scored `against` a baseline other than the primary
(the plot draws only the primary), the final-value ring of a `points` leaf
with no declared points (its two arrows do move the criterion's tolerance),
or any handle of a metric that has no drag editor. And for a tolerance's
arrow or line:

- a side dragged across its target — "the upper side would cross its target",
  "the before side would cross its point's time", "the early side would cross
  its event's time". A side of exactly zero is taken;
- a `rel` side where there is nothing to be a fraction of: "the target is
  zero" (a checkpoint), "the reference is zero at t=…" (a tube), "the
  baseline scalar is zero";
- a peak's window that would reach 0 Hz, whichever arrow moved — a number is
  both sides, so its upper arrow dragged past twice the frequency is one —
  and the peak's own ring where its `abs` window would ("the window would
  reach 0 Hz"; of a window that was there before the drag — a pair's lower
  side under its upper arrow, an `abs` window under its ring — "the window's
  lower side already reaches 0 Hz");
- a side measured from the baseline on a leaf scored `against` another one,
  or with no baseline at all;
- a tolerance that would be no finite number.

Where two shapes overlap, the one drawn on top takes the drag — of two
checkpoints on the same spot, the later one's ring covers the earlier one's
(their arrows are staggered apart). An arrow that no step clears stays under
the one drawn over it: the third on one spot, where its checkpoint's
tolerance is under 8.5 px across the arrow's direction — three steps would
take it out of the hover area. Zoom in to separate them, or use the fields.

Beside the handles, the editor draws a table for each list field of the open
criterion — headed by the field's label: Control points (`tube_points`),
Declared points (`points`), Declared events (`events`), Peaks (`peaks`) — one
row per entry and a control per key. Every cell
change stages the whole list as one `replace` of the leaf. A blank optional cell
leaves its key out (a checkpoint with a blank `value` follows the baseline); a
blank required cell (`time` on a tube point or an event, `freq` on a peak) is
marked on the cell as `<key> is required`, and nothing in that table is staged
until it is filled. A tolerance cell — a row's `tolerance`, a checkpoint's
`time_tolerance` — is the form's two fields and link toggle, its sides named
for what they are: `−` / `+`, `before` / `after` a checkpoint's time, `early`
/ `late` for an event. Left empty it is left out of the row, which then
follows the criterion's tolerance (a peak has none to follow: 1 % each
side). With one side of an unlinked pair blank,
the blank says "required" and nothing in that table is staged until it is
filled or its row removed; the cell stays blank and marked through Add row,
Remove row, a drag on the plot and an edit in the form above, each of which
redraws the table. A negative tolerance is refused where it is typed — "row
0: tolerance must not be negative; got −0.5", the rows counted from 0 — the
cell keeps what was typed, and the row goes back to what it held when the
cell was entered. A row too long for the editor wraps inside itself.
`Remove row` drops the row. `Add row` copies the last row;
where the table has a time, the new row's goes at the midpoint of the largest
gap between the reference's first and last samples and the existing rows'
times, so it stays clear of them (with no reference, the largest gap between
the rows' times, or `1.0` past the last time when fewer than two rows have
one). On an empty table that is the middle of the reference, and the row has
required numbers `0.0`, optional ones blank and each choice at its default — a
tube row gets, as `upper` and `lower`, the constant width the tube is scored
by (`tube_abs` on `band`, `tube_rel` on `rel`, a number or a pair), so the
click alone changes no verdict; an `abs` tube has no constant width and its
first row is `0.0` / `0.0`, literal bounds to set. Save writes
table and drag edits like any other pending op.

**Save, Discard, Export.** Save applies exactly the pending ops through the same
read-modify-write `dstf spec update` uses, and refuses when the file changed on
disk since it was loaded — offering to reload it or to keep your edits. Accept
makes the same check and is refused in the same dialog: a baseline is written
with the test's entry as the tool holds it (its `baseline_points`, the
comparison settings stored beside the data), and after an edit of the file by
hand that is no longer the entry on disk. Discard
drops the pending ops and restores the loaded verdicts. Export writes the
multi-model envelope to `<work_dir>/spec_patch.json`, which `dstf spec update`
applies unchanged.

The dashboard link opens `<work_dir>/dashboard.html` served by the tool itself
(a browser refuses a `file://` link from an `http://` page), so it shows the
last run of the project that is open.

---

## Typical workflows

### First-time setup for a new library

```bash
# 1. Create reference directory and testing.json
mkdir -p /path/to/MyLib-Tests/ReferenceResults
# Edit testing.json with source_path, backend, etc.

# 2. Verify discovery
uv run dstf --config /path/to/testing.json discover

# 3. Run all tests and accept as initial baselines
uv run dstf --config /path/to/testing.json run --accept
```

### Routine regression testing

```bash
# Run tests after code changes
uv run dstf --config testing.json run

# If tests fail, review interactively
uv run dstf --config testing.json run -i
```

### CI integration

```bash
# JUnit output for CI systems. The exit code is 1 when a test FAILs or
# SIM_FAILs, and when no test ran at all; a NO_REF test, and a NOT RUN one
# (a skipped testcase) beside tests that ran, leave it 0.
uv run dstf --config testing.json run --report-format junit

# HTML report — the same dashboard page `run --report` produces, at a path you pick.
# Its per-test links resolve only when the output lands inside the work dir.
uv run dstf --config testing.json run --report-format html --output report.html
```

A failed testcase's `<failure message>` counts the criteria that failed
(`2 criteria failed`), one line each in the body: the first line of that
criterion's block in the console's failure details — the variable, the
metric, its window, its baseline — then the criterion in the metric's own
words, or why it was not scored:

```
x (unitTests.x[1]): nrmse · window to 2.5 s · against primary — NRMSE 4.264e-02 vs tolerance 1.000e-03 → FAIL
x (unitTests.x[1]): range — Not scored: needs at least one of min_value / max_value
```

A failure under a `warn`
cannot fail the test and is not one of them: the message names it beside them
(`1 criterion failed, with 1 advisory failure`) and its line starts
`advisory —`. A test can fail with every criterion passing — at a `weighted`
group whose sum is over its threshold: the message is then that group's line
(`weighted group at metrics.children[1]: weighted sum 2.500000e-03 is not
below its threshold 0.001`), and the console's Failure Details prints the same
line under the test. A testcase that PASSES with failures under a `warn` has
no `<failure>` and is counted in nothing; its `<system-out>` says `1 advisory
failure (under `warn`: it cannot fail the test)` over the same `advisory —`
lines.

---

## Reference file structure

```
ReferenceResults/
├── testing.json              # Configuration
├── test_spec.json            # External test definitions (optional)
└── Dymola/
    └── windows/
        ├── ref_0001.json     # One file per test (self-contained)
        ├── ref_0002.json
        └── ...
```

References are partitioned by backend and OS because different solvers and platforms produce numerically different results. The bundled examples ship these partitions: `examples/modelica` — `Dymola/linux`, `Dymola/windows`, `OpenModelica/linux`; `examples/fmu` — `FMPy/linux`, `FMPy/windows`; `examples/julia` — `Julia/linux`; `examples/python` — `Python/linux`. A first run on an OS the backend has no partition for reports every test `NO_REF` until `run --accept` stores its baselines there. No manifest file is needed — each ref file contains its own `model_id`, `test_id`, `status`, `date_added`, and `last_updated`. The index is rebuilt in memory by scanning ref files at startup.

Each ref file also contains simulation params, statistics, diagnostic trajectories (`CPUtime`, `EventCounter` when `OutputCPUtime := true;`), and the time/variable data. Its `sampling` record says how many of the run's points the file keeps and the precision they were written from (see "The points a baseline stores"); a file without one was stored before the record existed. A variable entry is `{"index", "name", "expression", "values"}`: the name is the result file's, the expression is the label beside it (`""` when the source wrote none), and `expression` is always written — an entry without the key reads as one with no expression, and one whose `name` is the expression text itself as a missing baseline (see "A variable's name, and its expression").

**Status field:** Each ref file has a `status` field:
- `active` — normal, included in test runs
- `skip` — temporarily excluded from comparison
- `obsolete` — pending deletion (list with `manifest cleanup`, delete with `manifest cleanup --apply`)

---

## Development

### Setup

```bash
# Build .venv from the lock. The project installs editable (source changes take
# effect immediately) and every dependency is pinned to the same lock CI uses.
# --locked fails loudly if uv.lock has drifted from pyproject.toml, instead of
# silently re-locking and giving you an environment nobody reviewed.
uv sync --extra dev --locked

# Plus the optional backends. uv sync prunes whatever you did NOT name on this
# command line — being present in the lock does not save it — so ask for every
# extra at once rather than adding one later.
uv sync --extra dev --extra fmpy --extra om --locked
```

Bare `uv sync` is not the setup command: it installs the runtime dependencies
only, and removes pytest, ruff, mypy, pytest-cov and scipy if they were there.

Change a dependency in `pyproject.toml` and the lock goes stale: run `uv lock`
and commit `uv.lock` alongside it. Skipping that fails CI and not your machine —
`uv run` re-locks silently, so your tests stay green while `--locked` refuses in
CI.

Two dev tools are outside the lock. Install them like this — note the second
lands outside `.venv`, which is what keeps a sync from touching it:

```bash
# Browser-driven interactive tests
uv pip install pytest-playwright && uv run playwright install chromium
# Commit hooks (D88), installed as a standalone tool rather than into .venv
uv tool install pre-commit && pre-commit install

# ...after which every sync needs --inexact to leave pytest-playwright alone
uv sync --extra dev --locked --inexact
```

Without `--inexact` the Playwright package is removed and those tests go back to
skipping. The board stays green; what moves is the arithmetic — the module-level
`importorskip` collapses the whole file into a single skip (verified: running
that file with Playwright absent reports `1 skipped`), so the collected count
drops by every test in it.

Reach for `uv pip install -e ".[dev]"` instead only when you are deliberately
outside this workflow: installing DSTF into an environment you manage yourself
(conda, system Python, another project's venv), or testing against
newer-than-locked dependencies without touching `uv.lock`.

In that second case, do not then reach for `uv run`. It does not prune, but it
*does* reconcile locked packages back to their locked versions — so the newer
package you installed by hand is swapped out from under the very test you meant
to run. Use `uv run --no-sync pytest`, or call `.venv/bin/pytest` directly.

### Running the test suite

```bash
# Run all tests
uv run pytest

# Verbose output
uv run pytest -v

# Run a specific test file
uv run pytest tests/test_comparator.py

# Run a specific test class or method
uv run pytest tests/test_comparator.py::TestCompareTrajectories::test_identical_with_single_event
```

Tests cover: NRMSE comparison with event handling, config path resolution, `.mo` parsing, test spec parsing, manifest management, reference store round-trips, dslog parsing, `.mat` file reading, and variable pattern matching.

Tests that require Dymola are marked with `@pytest.mark.dymola` and can be skipped with:

```bash
uv run pytest -m "not dymola"
```

### Testing ModelicaTestingLib itself

The project includes `examples/modelica/ModelicaTestingLib/`, a small Modelica library used both as a test fixture and as a reference implementation: thirteen tests, one per comparison mode and combinator, among them `HeatRodTest` — the one whose variable is an array (see the folded criteria row, above). To regression test it (requires Dymola or OpenModelica; `HeatRodTest` has baselines for `Dymola/linux` and `OpenModelica/linux` and reads `NO_REF` on Windows Dymola until one is accepted there):

```bash
# Run and compare against stored references
uv run dstf --reference-root examples/modelica/ModelicaTestingLib/Resources/ReferenceResults run

# Accept new baselines after intentional changes
uv run dstf --reference-root examples/modelica/ModelicaTestingLib/Resources/ReferenceResults run --accept
```
