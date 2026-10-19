# Install and a first run

This page installs dstf and runs the Python example library, which needs no
simulator: its tests are Python functions. It takes a few minutes.

## Install

dstf is a command-line tool. Install it with [uv](https://docs.astral.sh/uv/)
or [pipx](https://pipx.pypa.io/), which give it an environment of its own:

```console
$ uv tool install dynamic-systems-testing-framework
$ dstf --version
```

or

```console
$ pipx install dynamic-systems-testing-framework
```

The package is `dynamic-systems-testing-framework`; the command is `dstf`.
Three parts are optional extras: `author`, for `dstf author`, the browser tool
for writing criteria, and `fmpy` and `om`, for the FMPy and OpenModelica
backends (their setup pages say which). Name them at install:

```console
$ uv tool install "dynamic-systems-testing-framework[author]"
```

## Get the example

The example libraries are in the repository, not in the package:

```console
$ git clone https://github.com/ORNL-Modelica/DynamicSystemsTestingFramework.git
$ cd DynamicSystemsTestingFramework
```

The Python example's tests run in the Python that is first on your `PATH`,
and two of them use numpy and scipy. Give them an environment that has both:

```console
$ python3 -m venv .venv-example
$ . .venv-example/bin/activate
$ pip install numpy scipy
```

(On Windows, activate it with `.venv-example\Scripts\activate`.)

## Run it

```console
$ dstf --config examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json run
```

dstf reads the project's `testing.json`, finds three tests in its
`test_spec.json`, runs each, and compares each result with the baseline
stored beside it. A browser tab opens on the dashboard as the run starts
(`--no-browser` prints its link instead). The console ends with one verdict
per test:

```text
Running 3 tests (parallel=1, timeout=60s)
Backend: Python · linux · DSTF 1.2.0
...
  PASS  [0001] PythonTestingLib.Examples.ConstantCsv
  PASS  [0003] PythonTestingLib.Examples.PointsCheck
  PASS  [0002] PythonTestingLib.Examples.SimpleRamp

Results: 3/3 passed
```

The exit code is 0: every test passed.

## What it wrote

Everything a run writes goes to the work directory, here
`testing_output/PythonTestingLib/Python/linux/` under the directory you ran
it from:

```text
testing_output/PythonTestingLib/Python/linux/
├── dashboard.html          the suite's page: one row per test
├── verdicts.json           each test's verdict and criteria, for a script
├── status.json             what became of each simulation
├── batch_manifest.json     the run's record
├── reference_manifest.json which baseline file is which test
└── test_0001/ ...          each test's result files
```

Nothing under `examples/` changed. The baselines the run compared against
are under the reference root, `Resources/ReferenceResults/Python/linux/`, one
`ref_NNNN.json` per test; only accepting a result writes there.

## Make it fail

Open `examples/python/PythonTestingLib/Examples/SimpleRamp.py`, change the
ramp's slope from `2.0` to `2.1`, and run again with `--report`, which also
writes a page per test:

```console
$ dstf --config examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json run --report
```

```text
  PASS  [0001] PythonTestingLib.Examples.ConstantCsv
  PASS  [0003] PythonTestingLib.Examples.PointsCheck
  FAIL  [0002] PythonTestingLib.Examples.SimpleRamp

Results: 2/3 passed
  Failed: 1

Failure Details:
--------------------------------------------------------------------------------

  [0002] PythonTestingLib.Examples.SimpleRamp
    x: nrmse · against primary
      NRMSE 2.894e-02 vs tolerance 1.000e-02 → FAIL
      RMSE:               2.893959e-01 (range 1.0000e+01)
      max |err|:          5.000000e-01 at t=5
      final:              ref=1.000000e+01  act=1.050000e+01
```

The exit code is now 1. The failure says which criterion failed — variable
`x`, scored by `nrmse` against the primary baseline — what it measured
against what it allows, and where the run is furthest from the baseline. The
dashboard shows the same verdicts, and the test's name links to its page:

![The dashboard after the failing run: two tests pass, SimpleRamp fails](../img/dashboard.png)

Change the slope back to `2.0` and the next run passes again.

## Next

- [Read the results](results.md): the console, the dashboard, the per-test
  page and the files a script reads.
- [Set up testing.json for your library](testing-json.md).
