# Usage Guide

## Setup

### Configuration file (`testing.json`)

Create a `testing.json` in your reference results directory. This is the single entry point for the tool — it tells it where the library is, which backend to use, and how to run tests.

```json
{
  "source_path": "../../MyLibrary/MyLib",
  "backend": "Dymola",
  "backends": {
    "Dymola": [
      "C:\\Program Files\\Dymola 2026x\\bin64\\Dymola.exe",
      "C:\\Program Files\\Dymola 2025x Refresh 1\\bin64\\Dymola.exe"
    ],
    "Dymola 2025": [
      "C:\\Program Files\\Dymola 2025x Refresh 1\\bin64\\Dymola.exe"
    ],
    "Dymola 2026": [
      "C:\\Program Files\\Dymola 2026x\\bin64\\Dymola.exe"
    ]
  },
  "dependencies": [],
  "test_spec": "test_spec.json"
}
```

**Fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `source_path` | No | Path to library source location (Modelica package.mo directory, FMU directory, etc.). Relative to `testing.json` location. Can be provided via `--source-path` instead. |
| `backend` | No | Backend name (default: `"Dymola"`). Use named entries like `"Dymola 2025"` to target specific versions. |
| `backends` | No | Map of backend names to candidate executable paths (first existing path wins). Falls back to system PATH. |
| `dependencies` | No | Paths to dependency library roots loaded before simulation. Relative to `testing.json` location. |
| `backend_setup` | No | Modelica commands run after library loading. For user-specific settings only — `OutputCPUtime` and translation log capture are handled automatically by the Dymola backend. |
| `test_spec` | No | Path to external test definitions file. Relative to `testing.json` location. When unset, `<reference_root>/test_spec.json` is used if it exists — the file `add` writes — and every command that discovers tests reads it. |
| `diagnostic_variables` | No | Variables auto-captured but not compared (default: `["CPUtime", "EventCounter"]`). |
| `reference_root` | No | Override where references are stored. Defaults to the directory containing `testing.json` if it's named `ReferenceResults`, otherwise `<repo>/Resources/ReferenceResults`. |

All relative paths resolve from where `testing.json` is located.

The former spellings `simulator`, `simulators` and `simulator_setup` are no
longer read. A `testing.json` still containing one is rejected at load with a
`ConfigError` naming the replacement, rather than parsing on while every
setting under that key silently does nothing. There is no alias and no
migration path: the old key never works again, it just says so.

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

Modelica match types: `component-instantiation` (`component_name`), `extends` (`class_pattern` glob). Field sources: `parameter` (from matched component), `constant` (literal value), `experiment-annotation` (from standard `experiment(...)` block). Per-field merge with last-writer-wins lets a user recognizer override individual fields without disabling bundled. To turn the bundled recognizer off entirely (e.g. when a dependency lib's example tests would otherwise be discovered), add its name to `disable_bundled`: `["modelica:bundled-unit-tests"]`. See `examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json` for a working demo.

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

#### Two-layer contract: annotation vs `test_spec.json`

Modelica simulation parameters resolve from two layers, in order:

1. **`experiment(...)` annotation** on the model — the source-of-truth defaults the model author encoded. Recognized fields: `StopTime`, `Tolerance`, `__Dymola_Algorithm` (→ `method`), `NumberOfIntervals` (or `__Dymola_NumberOfIntervals`), `Interval` (→ `output_interval`).
2. **`simulation.*` block in `test_spec.json`** — per-test override. Any field set here wins over the annotation.

Resolution rule: **user omits the field → annotation if present, else simulator default. User provides the field → it is used.** No third layer; no per-field "force annotation" toggle (delete the override to defer to the annotation).

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
with no mapping — and a soft-check or sibling-backend overlay, which never
has one — is matched by name, then by expression, because another tool's
result file cannot be expected to spell a positional name.

A `test_spec.json` written before this change names expressions where it now
needs names, and a baseline accepted before it is stored under the expression
too. `dstf migrate-variable-names` rewrites both:

```bash
# Report what would change — nothing is written
uv run dstf --config testing.json migrate-variable-names

# Write it
uv run dstf --config testing.json migrate-variable-names --apply
```

It walks every `<reference_root>/<Backend>/<os>/` partition (primary refs and
`soft_checks/`), rewrites each UnitTests entry whose stored name matches that
index's expression, gives every other entry the `expression` field, and
renames the matching override keys — in the baseline's own `comparison`
section as well as the spec's — plus the leaf variables and plot series in
`test_spec.json`. A second `--apply` rewrites nothing.

**Everything is read and decided before anything is written.** A baseline
the command cannot read — truncated, empty, not UTF-8, not a JSON object, or
shaped so the command cannot walk it (`variables` that is not a list of
objects, a variable `name` or `expression` that is not text, a `comparison`
or a `variable_overrides` that is not an object) — is reported by file and
reason rather than raising, and left exactly as it was; the rest of the run
is still decided and written around it, because no cross-file decision reads
a baseline's content and one stray junk file must not stall a tree of 900.
The exit code is 1, so fix the file and run it again. **The `test_spec.json`
is the exception**: while IT cannot be read **nothing at all is written**,
baselines included — its keys and the names the baselines are stored under
are one coordinated change. (A UTF-8 BOM is a reason to refuse neither: a
baseline — or a `test_spec.json` — a Windows editor saved is read and
migrated like any other, and written back without it.)

A **skip** leaves that one file exactly as it was, and there are five kinds:
an entry whose stored name matches neither the expression nor the migrated
name (reported by index, stored and expected text); an entry with no integer
`index`, or two entries claiming one index; an override key that is the
expression of *two* variables (`x={a, a}` — which one it meant is yours to
say); an override map holding both the old key and the new one; and two
*spellings* of one expression as override keys (`b[1, 1]` and `b[1,1]` name
the same variable, so renaming both would drop one). The command exits 1
when anything was skipped, when a file's model is not a discovered test, or
when a spec key was left alone — each of those on its own. A skip stops that
one file, not the run: the other partitions are still written.

Also reported, without moving the exit code: files that held CRLF line
endings and are written LF, as the store writes every baseline (each shows
as a whole-file diff); `soft_checks/ref_NNNN/` directories with no primary
beside them; spec entries whose model has no UnitTests variables here, so no
key of theirs was considered; and variables that need a re-accept.

It is a one-shot utility, **deleted at the v1.0 tag**. Until a baseline is
migrated, its variables read as missing baselines, and the log line says so
and names this command.

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

`tolerance_mode` is `"abs"` (default; `|actual - reference| <= tolerance`) or `"rel"` (`<= tolerance * |reference|`, falling back to the absolute test when the reference is 0). Declaring `min_value` and/or `max_value` switches the leaf to a **baseline-free bounds check** — the scalar must lie inside the inclusive bounds, and no reference is consulted:

```json
{"metric": "scalar", "variable": "pump.W", "quantity": {"reduce": "mean"}, "max_value": 5000}
```

A tree scores only what its leaves name. Every other tracked variable — one the
`variables` list or a pattern in it still matches — is stored, plotted and
never judged, and the comparison carries a structural warning, "Tracked
variables without a criterion", naming it on the console and the report page.
The flat path (no `metrics` block) has no such gap: it scores every tracked
variable. So a pattern such as `"variables": ["tank.*"]` keeps tracking new
matches after a tree exists, and the warning is how you learn one needs a leaf.

A window on a stats-path leaf is ignored — a stored scalar is not a time series. A stats-path leaf needs a `metrics` tree: in a flat `comparison.variable_overrides` entry the leaf only has the variable's own trajectory, so a string `quantity` there is a hard failure rather than a silent reduction of the wrong signal (a `{"reduce": ...}` override is fine).

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

Soft_checks are added from the CLI with `dstf import-baseline <model> soft_check <name> <source>`, or programmatically:

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

All commands accept these global options:

```
--config PATH          Path to testing.json (default: auto-search)
--source-path PATH     Path to library source location (Modelica package dir, FMU dir, ...)
--reference-root PATH  Path to reference results root
--test-spec PATH       Path to test_spec.json
--dymola-interface PATH  Path to Dymola's Python interface archive or its directory
```

They work on **either side of the subcommand** — `dstf --config x run` and
`dstf run --config x` are equivalent. A value given after the subcommand wins.

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

Output shows model ID, variable count, stop time, solver method, and source (unit_tests, spec, or both).

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
| `--backend-path PATH` | Override the backend executable path |
| `--show-ide` | Show Dymola GUI instead of headless |
| `--tolerance FLOAT` | Override NRMSE comparison tolerance |
| `--default-points` | Use PointsMode by default (empty list ⇒ final-value-only check) |
| `--timeout SECS` | Per-test timeout in seconds (default: 600) |
| `--work-dir PATH` | Override output directory |
| `--report-format` | `console` (default), `junit`, or `html` (the dashboard page, written to `--output`) |

**Interactive mode (`-i`) actions:**

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
recorded as pending — a run aborted while it was in flight — is refused with
`re-run before accepting`; a result the store declines (a trajectory carrying
`NaN`, say) is refused with that reason and the review moves on to the next
test; and a simulate-only test stores no baseline at all
(`simulate-only test: no baseline stored`), since there is nothing to compare
it against.

### Live progress + final report

DSTF writes a single `dashboard.html` to your `work_dir` that serves as both the live progress page during a run and the final report after comparison.

- **During a run**: the page is re-written on every state change and reloads itself every 5s (a meta refresh — a script fetch is blocked on `file://`). It stays live until the command that started the run has finished with it.
- **After comparison**: the same page is re-rendered in final mode — the refresh is dropped and each row carries its verdict and its numbers (Worst NRMSE, Variables, Warnings) and its Translate / Sim / Total wall times. The Reference link comes from the per-test report, so it is filled only with `--report`, which also writes the per-test interactive deep dives at `reports/test_NNNN/interactive.html`, linked from the Model column.
- **Which rows**: a live page lists the tests the running run holds. A final page lists the last run's tests and then every other test the command scored, in the work dir's manifest order. So `compare` after a `--filter` run, a `--merge` run, and a Rerun in `dstf author` list every test with its verdict; a plain `--filter` run without `--merge` lists only its own tests, because those are all it scored. A test the last run did not run keeps its per-test report (and its Reference link) while that report is newer than the test's own last run. The status bar of such a page names both counts: `Final report · 331 tests · last run: 1 test in 0.4s`.

The **Translate (s) / Sim (s) / Total (s)** columns — on the dashboard and on the Suite page of `dstf author` — are wall-clock seconds. Total is always the whole test; what Translate and Sim time depends on the backend:

- **Dymola** (persistent, the default): DSTF's own clock around the translate call and around the simulate call.
- **OpenModelica**: omc's own record — Translate sums its frontend, backend, code-generation and compile phases, Sim is its simulation phase.
- **Julia** (persistent, the default): the Julia worker's own time for the simulate call, as Sim.
- **FMPy, Python, Julia `--batch`**: the whole test's wall, as Sim — there is no translation step to time — so Sim equals Total.
- **Dymola `--batch`**: none — a `.mos` batch times the batch, not each test, and a per-test average would show a time no test took.

A run keeps these, and its other statistics, beside each result in `test_NNNN/run_stats.json`, so a later `compare`, a `--merge`d test and a reload in `dstf author` show them too. A Dymola result from before that file existed still shows its translation and simulation statistics, read back from the `dslog.txt` and `translation_log.txt` beside it; the logs hold no wall times. Where no wall time was kept — a test last run before that file existed, or a Dymola `--batch` run — Sim (s) shows the result file's own CPU time instead, marked: the number is followed by a small **cpu** tag and its tooltip reads "CPU time from the result file (no wall time was kept)". That is the final value of Dymola's `CPUtime` (DSTF sets `OutputCPUtime := true`), so only Dymola results carry one; on any other backend such a row reads "—" until it is re-run. A kept wall time always wins, and Sim (s) sorts and filters on the number whichever it is.

A row reads **SIMULATED** when its simulation ran and nothing has scored it: during a run, between a test finishing and the compare phase, and on a page written without scoring at all — after `run --accept` (the results were stored as baselines, not compared), an aborted run, or a rerun from `dstf author`. SIMULATED is not a status: it is in no count pill.

A row reads **NOT RUN**, in grey, when its test has no result: the last run never ran it, or started it and never finished it (a run stopped by Ctrl-C or a crash leaves its unfinished tests so). That is not a failed simulation — a test whose run finished and failed, timed out or could not translate reads SIM_FAIL — and it is not a failure: `run` and `compare` exit 0 when every test that ran passed, whatever is NOT RUN beside them; the console lists it as `NOT RUN` and JUnit writes it as a skipped testcase (`<skipped message="not run"/>`). Rerun it to give it a verdict.

The count pills along the top are the status filter: click one to show only its rows (PASS, FAIL, SIM_FAIL, NO_REF, TIMED OUT, QUEUED, RUNNING, NOT RUN; `warnings` shows the rows with any), `Total` to show every row again.

Every column but Status has a typed filter under its header:

- **Text columns** (Test, Reference, Model, Resolution, Worker, Message) match a **substring** by default, case-insensitively. A query containing `*` or `?` is a **glob** matched against the whole value (`*Pipe` matches `GenericPipe`, not `GenericPipe_Test`; `[` and `]` are literal). With the **`.*`** toggle pressed every text filter is a **regex**, searched (a bad one marks its box and filters nothing); with **`Aa`** pressed every text filter matches case. A row with no value in the column reads as empty to its filter, not as the `—` its cell shows — so a regex `.` on Message shows the rows that have a message.
- **Number columns** (Elapsed, Worst NRMSE, Variables, Warnings, Translate, Sim, Total) take a comparison — `>x`, `>=x`, `<x`, `<=x`, `=x` (a bare number is `=`) — or an inclusive range `a..b` (`1e-6..1e-4`). They compare the value, not its display text; a row with no value never matches. Variables filters on the number passed.

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
  the metrics emitted (a tube's band, a range's bounds, a points leaf's
  tolerance boxes, event windows, the FFT spectrum on its own axis pair, a
  scoped leaf's window as a shaded band). Zoom, hover and legend toggles are
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
apply a patch with `spec-update`.

`spec-update` reads a JSON-Patch envelope in either of two shapes. The
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

### `export` — Dump reference data

Exports stored reference data to JSON or CSV for external inspection.

```bash
# Export all references as JSON
uv run dstf --config testing.json export

# Export as CSV
uv run dstf --config testing.json export --format csv

# Export to specific file
uv run dstf --config testing.json export --output results.csv --format csv
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

# Write the ref ID → model ID mapping to the work directory
uv run dstf --config testing.json manifest dump
```

(The reference index is rebuilt automatically on every command — there's
no separate "rebuild" subcommand because there's nothing to rebuild.)

### `migrate-variable-names` — Move stored names off expression text

One-shot (D132): rewrites reference-JSON variable names and `test_spec.json`
keys from a UnitTests variable's expression text to its result-file name.
Dry-run by default. Full description under "A variable's name, and its
expression" above. **Deleted at the v1.0 tag.**

```bash
uv run dstf --config testing.json migrate-variable-names
uv run dstf --config testing.json migrate-variable-names --apply
```

### `add` — Add test to spec

Adds a model to `test_spec.json` without manually editing the file.

```bash
# Add a test with specific variables
uv run dstf --config testing.json add MyLib.Examples.NewTest --variables "pipe.T[1]" "tank.level"

# Add simulate-only (no variable tracking)
uv run dstf --config testing.json add MyLib.Examples.NewTest
```

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
`--source-path`, `--reference-root`, `--test-spec` and `--dymola-interface` are
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

**The preview** is the pending-op count's drawer: per edited test, in staging
order, its entry before and after its edits as `test_spec.json` will hold it
(`json.dumps(indent=2)`, what Save writes), added lines green and removed lines
red, each variable read by its label with its result-file name muted beside
it; how many edits the test has and what the edits do to its verdict ("2
edits · verdict would change PASS → FAIL"). When a test's first edit wrote its
implicit tree out, the diff is against that tree, and a note says Save writes
the tree too ("they score the same"); edits that cancel out (a context series
added, then Reset layout) say "These edits leave the entry as saved." Below the
tests, Save these / Discard these — the header's Save and Discard, over every
pending edit. Its close button or Esc closes it; under 1024 px it lies over
the header and the strip. An Esc while a menu or a dialog is open closes that
one, and the drawer stays; a second Esc, even while it is still fading out,
closes the drawer.

The **Suite page** is a table of the tests, with the dashboard's columns and
words (one row model, D136): Status, Test, Worst NRMSE, Variables, Sim (s),
Warnings, Edits (a test's staged edits, "2 unsaved") and Message, which takes
the width left over, are shown; the column chooser (the columns icon) shows or
hides the rest — Reference, Resolution, Translate (s), Total (s). Key, Worker
and Elapsed are a live run's and are not offered. One toolbar row above it
holds:

- **count pills** — `PASS n · FAIL n · SIM_FAIL n · NO_REF n · NOT RUN n · EDITED n`, and
  `RUNNING n` while a job runs (and, switched on, until you switch it off). A
  pill filters the table to its status; several pills show any of them, and
  EDITED adds the edited tests. The counts are over every test, whatever the
  filters; a pill with nothing to count is greyed.
- **view icons**, each with a tooltip — group by package / show flat, short /
  full names, choose columns — and the **`.*`** and **`Aa`** toggles, which make
  every column filter a regex and case-sensitive.
- at the right, **"N selected · ✕"** (✕ clears the selection; "(M hidden)"
  when a filter or a closed group hides selected tests — the actions still act
  on all of them), then **Accept** and **Rerun** as icons whose tooltip is the
  caption, "Rerun 2 selected tests", or the sentence saying what would enable
  it. A click opens the same confirm dialog.

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
edits staged and jobs run, keeping the sort, filters and selection. On a cold
library scan the rows the spec and the last run name appear at once, reading
"…" until the scan and the scoring reach them. A Rerun from the tool ends, like
`dstf run`, on a static `dashboard.html` — with the tool's verdicts, those of
any unsaved edit the Rerun ran with included. The dashboard is the report of
the last `dstf run` / `compare` or tool Rerun; Save, Discard and Reload do not
rewrite it. Save, Discard and Reload are refused while a job runs.

The **Test page** is one test. A second header row carries the way back to the
Suite, the model id, its verdict, and the backend and baseline it was scored
against. Under it, still in the part that stays at the top, the **status
strip** says the test's situation in one sentence and fills its one next
action: "Fails 2 of 3 criteria. If the new behaviour is right, accept it as
the baseline." (red, Accept filled); "No baseline yet — accept this result as
the first one" (Accept filled); "The simulation failed. Fix the model or its
settings, then rerun." and "No result yet — rerun to simulate it." (Rerun
filled; the verdict beside the model id then reads NOT RUN); "2 unsaved edits · verdict would change PASS → FAIL" (amber; Preview
and save opens the preview on this test, and Discard drops this test's edits
only — the header's Discard drops every test's); "Passes against its
baseline." (grey, Accept in the strip's ⋮ menu: there is nothing new to
accept). A criterion on a variable the last run did not record scores "not
found in the result" until a run records it, and the strip says so: with the
edit unsaved, "1 unsaved edit · verdict stays FAIL · 1 new variable: rerun to
score it", Rerun the one filled action (Preview and save and Discard quiet,
Accept in the ⋮); saved without a rerun, "1 new variable the last run did not
record: rerun to score it." (Rerun filled, Accept in the ⋮ — the baseline would
not hold it yet). Where the result FILE already holds the variable — Dymola's
and the JSON driver's (Julia, Python) record every variable — the tool reads it
again the moment the criterion is staged and scores it at once; the strip asks
for a rerun only on OpenModelica and FMPy, or for a name the file lacks. The count is of pending ops, so a flat test's first criterion edit
reads 2 — its tree written out, and the edit.

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
`±10%`, `in [a, b]`) instead; click the row to edit it there — tolerance,
the tolerance mode, bounds; there is no plot. A criterion reading a quantity no
statistic holds gets a row under "Other scored quantities". The `+` is grey,
with the reason as its tooltip, while a job holds the test, on a
simulate-only test, where nothing is scored yet, where the last run no
longer holds what it scored (rerun it), where the test's flat criteria
cannot become a tree without changing its scores (write a `metrics` tree
in `test_spec.json`), and under a `warn` or `weighted` root.

Below the card, the criteria tree is
closed to a single line — the root's verdict, what the root is, the tally, and
the first failures by name, each a button that jumps to its criterion — and
"Show tree" opens it to a row per node. Then come the test's plots — derived
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
ring to move a handle, or edit the fields; every change is staged, and Save
writes them. The criteria rows' header carries one `+` ("Add a criterion for
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
and its checks and every "+ new plot" are grey. A test with no result — never run, or
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

Below the Suite page's list is the job panel: while a job runs it reads
`running: <label>` and one row per test in the job, taken from the `status.json`
the run itself writes — model, status, phase, elapsed and the backend's detail
line — refreshed on a timer until the job ends. (A snapshot older than the job
belongs to the previous run and is not shown as this job's progress.) When the
job is over the panel reads `finished: <label>`, with one row per test it ran
carrying the backend's failure message where a result failed, or
`failed: <label> — <type>: <message>` when the job itself failed. Below that
come the lines the store writes reported, then links to the artifacts
capabilities wrote.

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
replaces of one leaf collapse into one pending op, so the pending count and the
exported patch carry one edit per leaf rather than a keystroke log (the preview
shows the entry as it will be saved either way).

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
A criterion on a variable the test did not track is what tracks it: every
backend records, stores and scores a criterion's variables after the test's
`UnitTests` variables and `variables` patterns, with no change to `variables`
(see the strip, above, for the rerun). A diagnostic's criterion scores its
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
since they need `k` or a threshold first. The tune icon, which opens that form,
is on k-of-n and weighted nodes only: and, or and warn have no fields.

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
write reports one line in the job panel — how
many baselines it stored, where, and every test it skipped with its reason —
and the results reload, so a re-baselined test's verdict flips in place.

**Rerun** simulates the selected tests again on a worker thread, as a job: the
rest of the tool stays usable, and the tests in the selection are locked until
the job ends (an edit to one of them is refused, since the run rewrites their
simulation directories). The panel names the backend the run will use — `runs
through Dymola` — lists the selected tests in project order, and the button
starts them. The job builds its own backend, the same choice a CLI run makes by
default: the persistent workers when the backend declares them, the batch path
otherwise. It narrates nothing of its own to the console `dstf author` was
started from — a logged warning or a simulator's own output is not narration
and still appears there.
One job at a time, and there is no cancel — a rerun ends when the backend ends
or its per-test timeout does. A job that finishes reloads the results and
rescores every verdict; a job that fails leaves the verdicts exactly as they
were and shows its error in the footer instead. Afterwards the dashboard link
shows the tests that reran and no others: one invocation writes one
`status.json`, so a partial rerun's page is that subset, exactly as a CLI
`--rerun ... --merge` leaves it.

**Edit on plot** is the rest of the open criterion's editor, scoped to that one
criterion: it is available when that leaf's metric has handles or a table — a
`tube`, `range`, `scalar`, `points`, `event-timing` or `dominant-frequency`
leaf. (An `nrmse` criterion says `nrmse has no on-plot handles` in the editor's
place.) On a test whose flat criteria cannot become a tree without changing
its scores, the editor draws no field and says that reason on one line, in
place of both Edit criteria and Edit on plot; a reason both would give — a
job holding the test — likewise appears once. The plot above the editor
carries rings for the open criterion alone, and a handle belonging to another leaf is refused by name. What drags:

- a green ring on each tube control point (upper and lower edge), each
  checkpoint and each declared peak (on the spectrum panel) — grab it at its
  centre; a ring grabbed off its centre is resized rather than moved, and snaps
  back with nothing staged;
- the horizontal line of a `range` or `scalar` bound — drag it up or down.

Releasing a drag stages one `replace` of the leaf the handle belongs to and
rescores the test; the plot redraws in place, so your zoom stays. Consecutive
drags of one leaf collapse into one pending op. What a drag means:

- A tube handle's vertical position sets that edge's width at the point's time
  (the value itself for `abs`, the distance from the reference for `band`, that
  distance over |reference| for `rel`); moving it sideways moves the control
  point's time.
- A checkpoint with an explicit `value` moves in both time and value. One that
  follows the baseline (no `value`) moves in time only, so a drag never turns it
  into a fixed-value check: dragged straight up or down it stages nothing, but
  a drag that also moves sideways — by more than a sub-pixel tolerance,
  1e-5 of the reference's time span — is a time move and stages the new time.
- A peak moves in frequency only; a bound moves in value only.
- A time dragged past where the handle can be drawn lands on that edge, and
  the rest of the drag still applies. For a tube point or a baseline-following
  checkpoint that is the first or last reference sample inside the leaf's
  window; for a checkpoint with a `value`, the window's own start or end.
- A number a drag writes into the leaf is rounded to 12 significant digits, so
  the spec file reads `0.3`, not `0.30000000000000004`.

After every drag the plot is redrawn from the staged spec, so anything the drag
did not change goes back where it was. Everything on the plot is draggable —
plotly.js offers no per-shape switch — but only the rings and bound lines edit:
a window, an event line or a checkpoint's tolerance box snaps back. A drag the
leaf cannot take snaps back too, and the reason shows above the editor's
tables, prefixed with the leaf's path: an upper edge dragged below the lower one, a
negative width, a `rel` width where the reference is zero, a control point moved
onto another's time, a lower bound above the upper one (a `scalar` leaf also
refuses equal bounds, which `range` accepts), a peak at zero or negative
frequency, a `band` or `rel` width or any time on a leaf scored `against` a
baseline other than the primary (the plot draws only the primary), the
final-value ring of a `points` leaf with no declared points, or any handle of a
metric with none to move (`event-timing`'s windows). Where two shapes overlap,
the one drawn on top takes the drag; zoom in to separate them.

Beside the handles, the editor draws a table for each list field of the open
criterion — `<path> · <metric> · <field>` for `tube_points`, `points`,
`events` and `peaks` — one row per entry and a control per key. Every cell
change stages the whole list as one `replace` of the leaf. A blank optional cell
leaves its key out (a checkpoint with a blank `value` follows the baseline); a
blank required cell (`time` on a tube point or an event, `freq` on a peak) is
marked on the cell as `<key> is required`, and nothing in that table is staged
until it is filled. `Remove row` drops the row. `Add row` copies the last row;
where the table has a time, the new row's goes at the midpoint of the largest
gap between the reference's first and last samples and the existing rows'
times, so it stays clear of them (with no reference, the largest gap between
the rows' times, or `1.0` past the last time when fewer than two rows have
one). On an empty table that is the middle of the reference, and the row has
required numbers `0.0`, optional ones blank and each choice at its default — a
tube row gets `upper` and `lower` `0.0`, a zero-width band to widen. Save writes
table and drag edits like any other pending op.

**Save, Discard, Export.** Save applies exactly the pending ops through the same
read-modify-write `dstf spec-update` uses, and refuses when the file changed on
disk since it was loaded — offering to reload it or to keep your edits. Discard
drops the pending ops and restores the loaded verdicts. Export writes the
multi-model envelope to `<work_dir>/spec_patch.json`, which `dstf spec-update`
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
# SIM_FAILs; NO_REF and NOT RUN (skipped testcases) leave it 0.
uv run dstf --config testing.json run --report-format junit

# HTML report — the same dashboard page `run --report` produces, at a path you pick.
# Its per-test links resolve only when the output lands inside the work dir.
uv run dstf --config testing.json run --report-format html --output report.html
```

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

References are partitioned by backend and OS because different solvers and platforms produce numerically different results. No manifest file is needed — each ref file contains its own `model_id`, `test_id`, `status`, `date_added`, and `last_updated`. The index is rebuilt in memory by scanning ref files at startup.

Each ref file also contains simulation params, statistics, diagnostic trajectories (`CPUtime`, `EventCounter` when `OutputCPUtime := true;`), and the time/variable data. A variable entry is `{"index", "name", "expression", "values"}`: the name is the result file's, the expression is the label beside it (`""` when the source wrote none), and `expression` is always written — an entry without the key was produced by a version older than that rule, which `dstf migrate-variable-names` brings forward.

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

The project includes `examples/modelica/ModelicaTestingLib/`, a small Modelica library used both as a test fixture and as a reference implementation. To regression test it (requires Dymola):

```bash
# Run and compare against stored references
uv run dstf --reference-root examples/modelica/ModelicaTestingLib/Resources/ReferenceResults run

# Accept new baselines after intentional changes
uv run dstf --reference-root examples/modelica/ModelicaTestingLib/Resources/ReferenceResults run --accept
```
