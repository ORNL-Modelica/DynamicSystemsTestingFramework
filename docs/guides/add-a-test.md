# Add a test

A test is declared in the model, in `test_spec.json`, or in both. This page
shows each, then how to give a new test its first baseline.

## In a Modelica model: the UnitTests component

A Modelica model is a test when it holds a component of a class named
`UnitTests` with an `n` and an `x={...}`: the expressions in `x` are what the
test tracks. Copy the component from the example library
(`examples/modelica/ModelicaTestingLib/Components/UnitTests.mo`) into your
library, or use one your library already has under that name, then drop it
into a model:

```modelica
model SimpleTest "Simple dynamics with two state variables"
  Real x(start=0, fixed=true);
  Real y(start=1, fixed=true);

  Components.UnitTests unitTests(
    n=2,
    x={x, y});

equation
  der(x) = 1;
  der(y) = -y;

  annotation (experiment(StopTime=10, Tolerance=1e-6, __Dymola_Algorithm="Dassl"));
end SimpleTest;
```

The model's `experiment` annotation says how it is simulated. dstf finds the
test when it scans the library:

```console
$ dstf --config testing.json discover
```

lists every test with what it tracks, its stop time and its solver. In the
result file the tracked variables are `unitTests.x[1]` and `unitTests.x[2]`,
and that is how a criterion or an override names them; `x` and `y` are their
labels on the reports.

## In test_spec.json

`test_spec.json` declares tests by name, beside the reference root's
`testing.json`. An entry needs only the model and what it tracks:

<!-- snippet: test_spec -->
```json
{
  "tests": [
    {
      "model": "MyLib.Examples.PipeTest",
      "variables": ["pipe.T[1]", "tank.level"]
    }
  ]
}
```

`variables` holds result-file names; `*` and `?` are wildcards. Anything the
entry leaves out comes from the model's `experiment` annotation, and a field
set under `simulation` wins over it:

<!-- snippet: test_spec -->
```json
{
  "tests": [
    {
      "model": "MyLib.Examples.PipeTest",
      "variables": ["pipe.T[1]", "tank.level"],
      "simulation": {"stop_time": 100, "timeout": 120},
      "comparison": {
        "tolerance": 0.01,
        "variable_overrides": {
          "pipe.T[1]": {"mode": "tube", "tube_width_mode": "rel", "tube_rel": 0.02}
        }
      }
    }
  ]
}
```

That lets a model demonstrate itself over a long run in the simulator's own
editor while the test runs a short one. An entry for a model that also holds a
`UnitTests` component adds to that test. Every key of an entry is on the
[test_spec.json reference](../reference/test-spec.md); a key that is not there
is an error that names it.

A test of an FMU, a Julia model or a Python function has no annotation to
read: its entry names the file it runs with `source`, relative to
`test_spec.json`:

<!-- snippet: test_spec -->
```json
{
  "tests": [
    {
      "model": "PythonTestingLib.Examples.SimpleRamp",
      "source": "../../Examples/SimpleRamp.py",
      "variables": ["x"],
      "simulation": {"stop_time": 5.0, "tolerance": 1e-6}
    }
  ]
}
```

## A test that does not run on one simulator

A test known not to work on one simulator yet — it times out there, or waits
on a fix — can be marked `skip` with a reason, so that it is not run there
and still shows on every surface with that reason. A longer time limit for
one simulator goes in the same place:

<!-- snippet: test_spec -->
```json
{
  "tests": [
    {
      "model": "MyLib.Examples.PipeTest",
      "variables": ["pipe.T[1]", "tank.level"],
      "simulation": {"timeout": 120},
      "backends": {
        "OpenModelica": {"skip": "times out in the pipe's initialization"},
        "OpenModelica/windows": {"skip": "no C compiler on the Windows runner"},
        "Dymola": {"timeout": 600}
      }
    },
    {
      "model": "MyLib.Examples.Unfinished",
      "skip": "waits on the new heat exchanger"
    }
  ]
}
```

- **`skip` on the entry** marks the test on every backend. **`backends`**
  holds a block per backend, keyed by the backend's name as the reference
  folders spell it (`Dymola`, `OpenModelica`, `FMPy`, `Julia`, `Python` —
  not a `testing.json` entry's name like `"Dymola 2025"`), or by that name
  and an operating system (`OpenModelica/windows`). A block holds `skip`, a
  reason, and `timeout`, whole seconds; a block that holds neither is
  refused.
- **The most specific wins**: the backend and OS's block, then the
  backend's, then the entry's own `skip` and `simulation.timeout`, then the
  run's `--timeout`. A block that says only `timeout` leaves the entry's
  `skip` to apply. There is no `false` that clears a mark: take the key out.
- **A name that is no backend or operating system is refused**, naming the
  ones there are. A key that names a plug-in backend is refused on a
  machine where that plug-in is not installed.

`dstf discover` lists every mark, on every backend, and ends the ones that
keep a test from running here with `— not run here`. Its header counts the
marks by where each is, then how many apply here: `Marked skip: 25 marks (25
on OpenModelica); none keeps a test from running on Dymola/linux, where this
config runs:`. After the marks it lists every `backends` block's `timeout`,
and ends the one that sets a test's limit here with `— applies here`:

```text
Time limits per backend:
  MyLib.Examples.PipeTest: 600 s on Dymola — applies here
```

A run on a backend and
OS a mark applies to does not simulate the test: its start says how many
tests it leaves out (`1 test marked skip is not run on OpenModelica/linux`),
and the test reads `SKIPPED` with its reason. A marked test never changes the
exit code, and a run of every test the marks let run is a full run.
`--include-skipped` (on `run` and `compare`) runs and scores the marked tests
like any other, each still carrying its mark, and after the report one line
each says `passes now — remove the mark`, `runs now but has no baseline to
score against — accept one, then remove the mark`, `has no result to score —
run it with run --only-skipped` (a compare of marked tests a run left out), or
`still does not pass`. An accept
stores nothing of a marked test it did not run (`not stored: … is marked skip
on OpenModelica (…)`); under `--include-skipped` it is accepted like any other.

`--only-skipped` (on `run` and `compare`) runs and scores the marked tests
and no other: a check of whether each mark can go, without running the
whole suite again. It chooses the marked tests first, so `--rerun` and
`--changed-since` then choose among them, and `--filter` and `--package`
narrow them as usual. Unless every test of the project is marked here, the
run is not a full one and says so, like a `--filter` run. When none of the
tests chosen is marked skip for this backend and OS, it says so (`No test is
marked skip on OpenModelica/linux.`, and `… among the tests --filter chose.`
beside a filter) and exits 0, writing nothing.

```bash
dstf run --config testing.json --only-skipped
```

A run whose every chosen test is marked simulates nothing. Without
`--merge` it is still this run: `status.json` records no test simulated and
the dashboard shows no row of the run before. A plain run writes
`verdicts.json` with the tests SKIPPED, and the dashboard lists them
SKIPPED; `-i` lists them SKIPPED too but leaves no `verdicts.json`; after
`--accept`, which stores nothing, there is no `verdicts.json` and the
dashboard has no rows. With `--merge` the last run's `status.json` is kept
and every test is scored on its last result, the marked ones SKIPPED; under
`-i` there is then no `verdicts.json` either.

## With dstf spec add

`dstf spec add` writes an entry for you, creating `test_spec.json` if it is
not there:

```console
$ dstf spec add MyLib.Examples.PipeTest --config testing.json --variables 'pipe.T[1]' tank.level
```

Without `--variables` the test declares nothing to score: it passes when its
simulation completes. That is a quick way to keep a whole package of examples
simulating:

```console
$ dstf spec add MyLib.Examples.Smoke --config testing.json
```

A Modelica model must be a model of the library: a name it does not hold,
the library itself, one of its packages, a function, a record, a connector or
a partial model is refused, with what it is, and nothing is written. A test
of an FMU, a
Julia model or a Python function names the file it runs with `--source` — a
`.fmu`, `.jl` or `.py` file — which the command requires there and writes
relative to `test_spec.json`:

```console
$ dstf spec add PythonTestingLib.Examples.SimpleRamp --config testing.json --source ../../Examples/SimpleRamp.py --variables x
```

## Its first baseline

A new test has no baseline: unless its criteria need none, it reads `NO_REF`.
Run it and accept its result as its baseline:

```console
$ dstf --config testing.json run --filter MyLib.Examples.PipeTest --accept
```

From the next run on it is compared with that result. [Baselines](baselines.md)
says more about accepting; [Criteria](criteria.md) says how to score it by
more than one tolerance.
