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

An entry may carry an optional `plots` key: the plots the per-test report and
the authoring tool draw for that test. Scoring ignores it.

```json
{
  "model": "MyLib.Examples.PipeTest",
  "variables": ["pipe.m_flow", "pipe.T[1]"],
  "plots": [
    {"kind": "time", "series": [{"var": "pipe.m_flow"}, {"var": "pump.N"}]},
    {"kind": "time", "series": [{"var": "pipe.T[1]"}, {"var": "pipe.T[6]"}, {"var": "pipe.T[11]"}]}
  ]
}
```

- **Absent** means one plot per tracked variable. `"plots": []` means no
  plots. `null` is refused.
- A series names **one variable of the result**, tracked or not (`pump.N`
  above is plot only: no criterion scores it) — the name its result file
  gives it, so a variable of the `UnitTests` component is `unitTests.x[i]`
  and not the text the model wrote for it (see "A variable's name, and its
  expression" below). A pattern (`*`,
  `?`) is refused; square brackets are part of a name. A variable may be on
  several plots, once per plot.
- `kind` is `"time"`, the only kind today, and is always written. Unknown keys
  inside a plot or a series are refused, so a typo is loud.
- Indexed LABELS that differ in their last integer index (`pipe.T[1]`,
  `pipe.T[6]`, …) draw as one family: one hue ramped light to dark by index,
  one legend entry (`pipe.T[1:5:11]`). The fold is over what a person reads,
  under one of two rules. A RESULT-FILE NAME folds on its last single-integer
  index and nothing else — a result file spells names no whitelist
  anticipates (`θ[1]`, `pipe₊T[1]` from Julia/MTK, `T-wall[1]`,
  `$DER.x[1]`), and a multi-dimensional index (`x[2,3]`) is not one array
  position. An EXPRESSION the model wrote must additionally be a plain
  indexed name, a negated alias or `der(...)` of one: a reduction over an
  array (`sum(core.Qs[6, :])`) is a quantity of its own, so two of them are
  two families. A text that carries a name in parentheses — two variables
  sharing one expression — belongs to no family either.
- A plot of several series shows no overlays and no error traces; both belong
  to a plot of one variable.
- A series the result does not hold draws nothing and the plot says
  "Not in this result: …". Which untracked variables a result holds depends on
  the backend: Dymola, Julia and Python result files hold every variable;
  OpenModelica's and FMPy's hold only the tracked ones.

**Components.UnitTests** (the in-source `n=N, x={...}` block) is a **discovery marker** carrying the variable list only. It deliberately does not carry simulation parameters — those live in the annotation (model-author-facing, editor-honored) or in `test_spec.json` (test-author-facing, override-only). Keeping the third layer out is what makes the contract teachable.

#### A variable's name, and its expression

A tracked variable is named by the **result file**, and everything that
addresses one — a `comparison.variable_overrides` key, a `metrics` leaf's
`variable`, a `plots` series' `var`, a baseline entry, the element ids on the
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
  comparisons' variables and every series of the test's plots, plot-only
  ones included. A variable reads as its expression, unless another name in
  that scope carries the same expression — `x={a.Q, a.Q}` is legal — and
  then the name rides with it: `a.Q (unitTests.x[1])`. A variable whose
  label IS its name is never spelled twice, so a plot-only variable
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
companions registered before mappings existed do, and what soft_checks and
the auto-discovered sibling-backend overlays always do, being DSTF's own data
under the same names. In order: the companion variable whose name is the
test variable's name, whose name is its expression, and whose own recorded
expression is either. If a step finds two candidates the companion draws
nothing for that variable and the report's overlay table says so, naming the
candidates and the `--map` that settles it.

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

- **During a run**: the page polls `status.json` every 2s and updates rows in place (no full reload, scroll position survives). A "Refresh now" button forces an immediate fetch.
- **After comparison**: the same page is re-rendered in final mode — the JS poll loop is dropped, and post-comparison columns are populated (Worst NRMSE, Variables, Warnings, Translate / Sim / Total wall times). Add `--report` for per-test interactive deep dives at `reports/test_NNNN/interactive.html` accessible from the Model column links.

The dashboard supports status filter buttons, per-column text filtering, and a 3-state sort cycle (none → sorted → reverse → none) on every column. Numeric columns sort descending first (largest first when triaging failures); text columns sort ascending first.

The **Resolution** column shows where each test's simulation parameters came from: `annotation` (from the `experiment(...)` annotation in the .mo file), `test_spec` (overridden in test_spec.json), or `mixed` (some fields from each — hover to see the per-field breakdown). This is the resolution-explainer for the two-layer contract documented above.

#### The per-test page

`interactive.html` is a **read-only** view of one test. It mutates nothing and
computes nothing: every number on it comes from a figure Python built or from
markup the server rendered. It shows

- one Plotly chart per plot of the test — its `plots` key, else one per tracked
  variable: the variables a criterion scores, in the test's order, then the rest
  of what the test tracks, which is the same default layout the authoring tool
  draws, so a tracked variable no criterion scores now has a section here too.
  Each chart draws actual, reference and the criterion geometry
  the metrics emitted (a tube's band, a range's bounds, a points leaf's
  tolerance boxes, event windows, the FFT spectrum on its own axis pair, a
  scoped leaf's window as a shaded band). Zoom, hover and legend toggles are
  live, which is how you read values off a plot. A section's heading reads
  its variables' labels, with their result-file names muted beside it when
  the two differ, and `data-variable` on the markup is always the name;
- an **overlay picker** above any plot whose variable a soft_check or companion
  carries data for — the overlay traces are already in the figure, and the
  checkbox toggles their visibility;
- an **error-overlay** dropdown per plot: signed error, absolute error, or
  per-point NRMSE. Signed and absolute error share the right axis
  "Error [signal units]"; NRMSE, dimensionless, has its own, "NRMSE [–]". Each
  axis is shown while a series on it is visible, whether the select or a legend
  click turned it on;
- the **metric tree**, with the CLI's verdict per node, a one-line criterion
  under each leaf, and the leaf's detail pairs (what was measured against what)
  — plus a summary line counting leaves passed and flagging advisory failures
  under a `warn` wrapper. A leaf reads `metric · <label>`: the expression its
  model wrote where there is one, the name otherwise;
- statistics, diagnostics and artifact sections, and plots for diagnostic and
  no-baseline variables;
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
`[author]` extra (NiceGUI); without it the command exits with the install hint.

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
backend, the open's progress, the pending-op count (click it for the drawer of
staged ops, grouped by model), Save, Discard, Export, a link to the dashboard
and Stop server.

The **Suite page** is the test list: a filter box, grouped-or-flat and
short-or-full name toggles, one chip per verdict that filters the list to it, a
checkbox per test for multi-select, the name as a link to that test's page, and
its status badge. Below the list are the actions over the checked tests —
Accept, Rerun — each labelled with its object, effect and count, and greyed
with the sentence saying what would enable it.

The **Test page** is one test. A second header row carries the way back to the
Suite, the model id, its verdict, the backend and baseline it was scored
against, and the actions on that one test. Under it, the criteria tree is
closed to a single line — the root's verdict, what the root is, the tally, and
the first failures by name, each a button that jumps to its criterion — and
"Show tree" opens it to a row per node. Then come the test's plots, one per
tracked variable unless the entry's `plots` key says otherwise, each drawn as
it scrolls into view. Under each plot are the criteria rows of the variables it
draws: a verdict pill, `metric · <the variable's label>`, the CLI's criterion
line, and that node's `+` / wrap / `−`. Clicking a row opens that criterion's editor below it,
one at a time, with the plot above as its editing surface — drag a ring to move
a handle, or edit the fields; every change is staged, and Save writes them.
Under the plot sits "Add a criterion for x" per tracked series, and Focus on
the plot's own toolbar shows that plot alone until "Show all N plots".

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
refuses. A dataclass
default is shown as a placeholder and never written, so an unset tolerance keeps
falling back to the test's. Structured fields — tube control points,
checkpoints, declared events and peaks — are shown read-only here and edited in
Edit on plot. Every control change stages one `replace` of the whole leaf and
rescores the test, so the figure and the verdicts follow the edit; consecutive
replaces of one leaf collapse into one pending op, so the drawer reads "leaf →
new leaf" rather than a keystroke log.

**Edit tree** is the controls on every node of the tree and on every criteria
row — `+`, wrap, `−`, and on a combinator edit and unwrap — and the "Add a
criterion for x" form under each plot. Each stages one op per structural
change: wrap a node in a combinator, change a combinator's kind and its `k` /
weights / threshold, add a leaf beside or under a node, remove a node (the root
has nothing to be removed from). A test the flat path scored gets its implicit
tree materialized by the same op. Each form opens in a small menu on its own
button, built from the tree as it is at that moment; a change the validator
refuses is said in place and stages nothing.

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
place.) The plot above the editor carries rings for the open criterion alone,
and a handle belonging to another leaf is refused by name. What drags:

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
# JUnit output for CI systems
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
