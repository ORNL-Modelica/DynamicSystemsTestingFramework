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

**Components.UnitTests** (the in-source `n=N, x={...}` block) is a **discovery marker** carrying the variable list only. It deliberately does not carry simulation parameters — those live in the annotation (model-author-facing, editor-honored) or in `test_spec.json` (test-author-facing, override-only). Keeping the third layer out is what makes the contract teachable.

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
| `a` | Accept this test's results as new baseline |
| `s` | Skip (don't update baseline) |
| `d` | Show detailed per-variable comparison |
| `p` | Generate plots and open in browser |
| `v` | Add variable patterns to track (updates test_spec.json) |
| `q` | Quit interactive review |

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

- one Plotly chart per variable — actual, reference and the criterion geometry
  the metrics emitted (a tube's band, a range's bounds, a points leaf's
  tolerance boxes, event windows, the FFT spectrum on its own axis pair, a
  scoped leaf's window as a shaded band). Zoom, hover and legend toggles are
  live, which is how you read values off a plot;
- an **overlay picker** above any plot whose variable a soft_check or companion
  carries data for — the overlay traces are already in the figure, and the
  checkbox toggles their visibility;
- an **error-overlay** dropdown per plot: signed error, absolute error, or
  per-point NRMSE, drawn on the right-hand axis;
- the **metric tree**, with the CLI's verdict per node, a one-line criterion
  under each leaf, and the leaf's detail pairs (what was measured against what)
  — plus a summary line counting leaves passed and flagging advisory failures
  under a `warn` wrapper;
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
    {"op": "add", "path": "/variables/-", "value": "tank.level"}
  ]}
]}
```

The single-model form — `{"model": ..., "patch": [...]}` — is read too, because
files in that shape exist. The authoring tool writes only the multi-model form.
Paths in each `patch` are relative to that model's entry in `test_spec.json`.

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

### `add` — Add test to spec

Adds a model to `test_spec.json` without manually editing the file.

```bash
# Add a test with specific variables
uv run dstf --config testing.json add MyLib.Examples.NewTest --variables "pipe.T[1]" "tank.level"

# Add simulate-only (no variable tracking)
uv run dstf --config testing.json add MyLib.Examples.NewTest
```

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

Each ref file also contains simulation params, statistics, diagnostic trajectories (`CPUtime`, `EventCounter` when `OutputCPUtime := true;`), and the time/variable data.

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
