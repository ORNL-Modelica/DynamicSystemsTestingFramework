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

A Modelica model must be a class of the library: a name it does not hold is
refused, and nothing is written. A test of an FMU, a Julia model or a Python
function names the file it runs with `--source`, which the command requires
there and writes relative to `test_spec.json`:

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
