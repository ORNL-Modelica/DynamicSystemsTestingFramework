# Dynamic Systems Testing Framework (DSTF)

[![quality](https://github.com/ORNL-Modelica/DynamicSystemsTestingFramework/actions/workflows/quality.yml/badge.svg)](https://github.com/ORNL-Modelica/DynamicSystemsTestingFramework/actions/workflows/quality.yml)

Regression and unit testing for time-dependent system behavior. Discovers tests, runs simulations, compares against versioned baselines, and reports. A simulator is a plug-in backend; the starting set covers **Modelica (Dymola, OpenModelica), FMU (FMPy), Julia/ModelingToolkit, and arbitrary Python**, and more are intended.

Library-agnostic: tests can be discovered from in-model annotations (`UnitTests` components), declarative `test_spec.json`, or both. Reference results are partitioned per backend / OS so the same library can be regression-tested across backends without ref drift.

Criteria are written in **`dstf author`**, a local browser tool over the same figures the report draws: edit a criterion beside the plot it scores — drag a handle, or Ctrl+click to add a row that settles on the data — give a tolerance a lower and an upper side, put one criterion on many elements of an array, queue tests on simulator workers that stay alive, and watch a running test's trace as Dymola or OpenModelica writes it. A default and a dark theme cover the tool, the dashboard and the report pages.

![DSTF dashboard](https://raw.githubusercontent.com/ORNL-Modelica/DynamicSystemsTestingFramework/main/docs/img/dashboard.png)

*The unified dashboard — live progress during a run, a static report after. Each report is stamped with the backend, tool + MSL version, and OS that produced it; the table gives per-test verdicts, worst-NRMSE, and phase timing.*

## What's in the box

- **Backends are plug-ins.** Five ship today — a start, not the full list — each with persistent-worker support where it pays off; `docs/extensibility.md` has the contract for adding one:

  | Backend | Source kind | Persistent | Notes |
  |---|---|---|---|
  | Dymola | Modelica `.mo` | ✓ default | Python interface; `--batch` selects the `.mos` path. Records translation statistics, so a change in a model's state count is a structural warning |
  | OpenModelica | Modelica `.mo` | ✓ default | OMPython ZMQ; `--batch` selects the `omc -s` path. Records timing statistics and the model's state count (from the init file omc generates), so a change in it is the structural warning it is on Dymola — against a baseline that records the count, which one accepted before this does at its next accept; system sizes and Jacobian counts are Dymola's alone |
  | FMPy | Pre-built FMU | per-test thread | `fmpy.simulate_fmu` in-process |
  | Julia / MTK | `.jl` ModelingToolkit | ✓ default | stdin-JSON pipe; Dyad untested |
  | Python | Any `simulate(stop_time, tol) -> dict` | subprocess-per-test | scipy, pandas, CSV loader, HTTP — anything |

- **7 comparison modes**, composable via a `MetricTree`:

  | Mode | What it scores |
  |---|---|
  | `nrmse` | RMSE over signal range — the default |
  | `tube` | Pass if signal stays inside a (constant or time-varying) envelope around ref |
  | `points` | Per-point checks at declared times with `time_tolerance` x-box and abs/rel y-tolerance |
  | `range` | Scalar `min_value` / `max_value` bounds — baseline-free |
  | `event-timing` | Match Modelica solver events against a declared list within per-event tolerance |
  | `dominant-frequency` | FFT peak matching against a declared peaks table — windowable |
  | `scalar` | One number — a reduction of the signal (`max`/`min`/`mean`/`sample_mean`/`final`/`integral`) or a stored statistic by dotted path — vs a baseline scalar or declared bounds |

  Combinators: `and`, `or`, `k-of-n`, `warn` (relax to advisory), `weighted`. Leaves can scope to a time window and target named baselines (`primary`, soft-checks, companions).

- **Read-only reports** — per-test Plotly HTML drawn from figures Python built: the trajectory with its criterion geometry (tube band, range bounds, point boxes, event windows, FFT spectrum), overlay picker, error overlays, and the metric tree with the CLI's verdict and score text per leaf. Plus a live dashboard during runs and JUnit XML for CI. The report shows, the CLI decides.

- **An authoring tool** — `dstf author` (the `[author]` extra) serves a Suite page and a Test page on `127.0.0.1`. Criteria are edited inline under the plot they score, staged, and written to `test_spec.json` on Save:

  | Feature | What it does |
  |---|---|
  | Two-sided tolerance | A tolerance is one number or `{"lower": a, "upper": b}`; on the plot, an arrow outside each side sets it |
  | On-plot editing | Drag a ring, an arrow or a bound line; Ctrl+click adds a row that settles on the data; a field's ⌖ arms it for the next click; a placed value is snapped to a readable step |
  | Simulation queue | Queue the selected tests: first in, first out, on workers that stay alive between runs; Remove a waiting test, Cancel a running one |
  | Live results | A running test's trace is drawn as Dymola or OpenModelica writes its result file; a cancelled or timed-out run is shown, never scored or accepted |
  | Criteria over an array | One criterion on the elements `1:5:20` of an array; identical criteria fold into one row, edited as one |
  | Themes | `"theme": "dark"` in `testing.json` for the dashboard, the report pages and the tool; the tool's ⋮ menu switches a browser tab live |

- **Three baseline roles** per test, partitioned on disk: **primary** (regression anchor, hard-fail), **soft_checks** (warn-wrapped cross-regression imports), **companions** (plot-only overlays, including cross-library).

## Install

End users:

```bash
uv tool install dynamic-systems-testing-framework   # then run plain: dstf ...
# or: pipx install dynamic-systems-testing-framework
```

Developers:

```bash
uv sync --extra dev --locked                             # core + pytest, from uv.lock
uv sync --extra dev --extra author --extra fmpy --extra om --locked   # plus the authoring tool, FMPy + OpenModelica extras
uv run dstf --help
```

`uv sync` installs the project editable and pins every dependency to `uv.lock`,
the same lock CI uses; `--locked` fails instead of silently re-locking when the
lock has drifted from `pyproject.toml`. It prunes anything you did not ask for
on that command line — being in the lock does not save it — so name every extra
at once.

For the Julia backend, install Julia 1.11+ and instantiate the demo project once:

```bash
curl -fsSL https://install.julialang.org | sh -s -- -y --default-channel 1.11
cd examples/julia/JuliaMtkTestingLib && julia --project=. -e 'using Pkg; Pkg.instantiate()'
```

The Python examples (`SimpleRamp`, `PointsCheck`) need scipy, which the `dev`
extra installs. Reach them through `uv run dstf ...`: the Python backend takes
the interpreter from `testing.json`'s `backends: {"Python": {"executables":
[...]}}` entry, and with none named uses the one running `dstf` — and the
shipped example names `python`, which is the first `python` on `PATH`, which
`uv run` points at `.venv`. Run the same examples from a tool-installed `dstf`
and that is whatever comes first on your PATH — a conda env, often — which
needs its own scipy.

## First 5 minutes

The repo ships demo libraries for every backend. Easiest first run is the Modelica demo:

```bash
# From the repo root:
uv run dstf --config examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json run
```

What this does:

1. Reads `testing.json` to find the demo library, the backend (Dymola or OpenModelica — first one whose binary exists wins), the reference root, and any per-test recognizers.
2. Discovers the demo tests via the bundled `UnitTests` recognizer plus declarative recognizers in the JSON.
3. Runs each in a persistent simulator worker, partitioning output under `testing_output/` by backend + OS.
4. Compares against `Resources/ReferenceResults/<Backend>/<os>/ref_NNNN.json` baselines.
5. Prints a console report with pass/fail per test.

To see the interactive HTML reporter:

```bash
uv run dstf --config examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json run --report
```

The dashboard URL is printed as a run starts (`Live progress: file://…`) and the page is opened in a browser tab; it reloads itself every 5s during the run. `--report` writes the per-test pages under `<work_dir>/reports/` and opens the dashboard again when they are written. `--no-browser` (on `run` and `compare`) opens no tab and still prints the link.

The examples ship baselines for these `<Backend>/<os>` partitions: Modelica — `Dymola/linux`, `Dymola/windows` and `OpenModelica/linux` (its array model, `HeatRodTest`, on the two Linux ones); FMU — `FMPy/linux` and `FMPy/windows`; Julia — `Julia/linux`; Python — `Python/linux`. A first run on an OS a backend has no partition for (OpenModelica, Julia or Python on Windows, say) reports every test `NO_REF` until `run --accept` stores its baselines there. A companion in the examples is another source's curve of the SAME model, drawn over the test's plot when its box is ticked on the report page, and each ships in the partitions named here and no other: in `OpenModelica/linux`, the Modelica `FrequencyTest`, `RangeCheckTest` and `MultiFrequencyTest` carry the Julia example's baseline of that model; in `Julia/linux`, the Julia `Frequency`, `RangeCheck` and `MultiFrequency` carry the OpenModelica one; and `MetricTreeTest` carries its closed-form solution in `Dymola/linux` and `OpenModelica/linux`. A Dymola run of the example therefore shows no Julia companion, and `Dymola/windows` ships no companion at all.

To repeat with another backend's demo library, swap the config path:

```bash
# Julia/MTK, Python, and prebuilt FMUs through FMPy
uv run dstf --config examples/julia/JuliaMtkTestingLib/Resources/ReferenceResults/testing.json run
uv run dstf --config examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json run
uv run dstf --config examples/fmu/testing.json run    # needs reference-fmus-binaries/
```

## Configuration cheat-sheet

`testing.json` is the single entry point. The full field reference lives in `docs/usage.md`; the headline shape:

```json
{
  "source_path": "../../MyLib",
  "backends": {
    "Dymola":       {"executables": ["C:\\Program Files\\Dymola 2026x\\bin64\\Dymola.exe"]},
    "OpenModelica": {"executables": ["/usr/bin/omc"]}
  },
  "test_spec": "test_spec.json",
  "dependencies": ["/path/to/SomeDep"],
  "tolerance": 1e-4,
  "diagnostic_variables": ["CPUtime", "EventCounter"]
}
```

The first `backends` entry that is found on this machine wins (no need for per-OS config files). `backend: "Dymola"` (or any named entry) forces a specific one. Each entry is a settings block: `executables`, plus the backend's own settings (Dymola's `show_ide`, `batch_size`, `interface`); an empty block `{}` means the backend is searched for.

## Common commands

```bash
# Discover tests without running
uv run dstf --config testing.json discover

# Run, compare, accept all current results as new baselines
uv run dstf --config testing.json run --accept

# Interactive review by category
uv run dstf --config testing.json run -i failed

# Filter (glob, comma list, or @file)
uv run dstf --config testing.json run --filter "Foo.A,Foo.B"
uv run dstf --config testing.json run --filter @rerun.txt

# Parallel + interactive HTML report (the Dymola block's `batch_size` in testing.json splits a --batch run into small batches)
uv run dstf --config testing.json run --parallel 4 --report

# Rerun only previously-failed tests, merge with last full run, regen report
uv run dstf --config testing.json run --rerun failed,sim-failed --merge --report

# Compare without re-simulating (uses last results)
uv run dstf --config testing.json compare

# Open the authoring tool in a browser (needs the [author] extra)
uv run dstf --config testing.json author

# Apply an RFC 6902 JSON-Patch envelope against test_spec.json — single- or
# multi-model form, from any source
uv run dstf --config testing.json spec-update spec_patch.json

# Manage the other baseline roles
uv run dstf --config testing.json companion add <model> <name> <path>
uv run dstf --config testing.json soft-check list <model>
uv run dstf --config testing.json import-baseline <model> <name> <source>   # soft_checks only

# Manifest / schema
uv run dstf --config testing.json manifest dump
uv run dstf --config testing.json manifest cleanup [--orphans] [--apply]   # dry-run by default
uv run dstf --config testing.json export-schema --output schema.json

# Which backends are installed, where each was found and by which rule, and whether this project's is ready
uv run dstf --config testing.json check
uv run dstf check Dymola        # one backend's full diagnosis (Dymola: the executable AND its Python interface)
```

`uv run dstf <command> --help` for each subcommand's full flag set.

## Where things live

| Doc | What's in it |
|---|---|
| `docs/usage.md` | The deep user guide — every command, every config field, every comparison mode |
| `docs/architecture.md` | Six-layer plug-in pipeline + current-state map to code |
| `docs/extensibility.md` | Plug-in contracts for Source / Discovery / Backend / Dataset / Metric / Combinator |
| `docs/vision.md` | Where the framework is going (target user base, non-Modelica futures) |
| `docs/decisions.md` | Authoritative decision log |
| `docs/ideas.md` | Backlog (unimplemented features, with explicit deferred-by-design entries) |
| `examples/{modelica,fmu,julia,python}/` | Working demo libraries — one per backend |

## CI integration

```yaml
- run: |
    uv run dstf --config ./testing.json run --report-format junit --output test-results.xml
- uses: actions/upload-artifact@v4
  with:
    name: test-results
    path: test-results.xml
```

For HTML reports as build artifacts, also pass `--report` and upload `dashboard.html` and the `reports/` directory from the work dir (`--work-dir` sets it).

## Status

v1.0. Five backends so far, with more intended; 4 demo libraries, and a comprehensive pytest suite (a Playwright subset drives the report pages and the authoring tool in a real browser). The report is read-only and mutates nothing: every number it draws comes from a figure Python built or a string the server rendered. Criteria are authored in `dstf author`, driven by the same figures and the same scorer as the CLI. Published on PyPI as `dynamic-systems-testing-framework`.
