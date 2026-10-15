# Dynamic Systems Testing Framework (DSTF)

[![quality](https://github.com/ORNL-Modelica/DynamicSystemsTestingFramework/actions/workflows/quality.yml/badge.svg)](https://github.com/ORNL-Modelica/DynamicSystemsTestingFramework/actions/workflows/quality.yml)

DSTF is regression and unit testing for the behaviour of time-dependent
systems: Modelica libraries, FMUs, Julia models, or anything Python can
compute. It finds a library's tests, simulates them with a backend — Dymola,
OpenModelica, FMPy, Julia or Python, or one a plug-in adds — and scores each
result against a stored baseline by the criteria you write. It reports a
verdict per test on the command line, as JUnit XML for CI, and as a dashboard
and a page per test in the browser.

DSTF is in beta: the command line and the testing.json keys may still change
between minor versions; the changelog says when.

## Install

```console
$ uv tool install dynamic-systems-testing-framework
```

or `pipx install dynamic-systems-testing-framework`. The command is `dstf`.
The `author` extra adds `dstf author`, a browser tool for writing criteria
beside the plots they score; `fmpy` and `om` add what the FMPy and
OpenModelica backends drive.

## A first run

The Python example library needs no simulator. From a clone of this
repository, in a Python environment with numpy and scipy:

```console
$ git clone https://github.com/ORNL-Modelica/DynamicSystemsTestingFramework.git
$ cd DynamicSystemsTestingFramework
$ python3 -m pip install numpy scipy
$ dstf --config examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json run
```

It runs three tests, compares each with its stored baseline, prints a verdict
per test and opens the dashboard:

![The dashboard: one row per test with its status, worst NRMSE, criteria passed and times](https://raw.githubusercontent.com/ORNL-Modelica/DynamicSystemsTestingFramework/main/docs/img/dashboard.png)

## Documentation

<https://dstf.readthedocs.io/> — a first run, setting up `testing.json` for
your library, adding tests, reading results, baselines, criteria, CI, a page
per simulator, writing a backend plug-in, and a reference of every command,
key and comparison mode.

## Changelog and licence

What changed in each version is in
[CHANGELOG.md](https://github.com/ORNL-Modelica/DynamicSystemsTestingFramework/blob/main/CHANGELOG.md).
DSTF is released under the MIT licence:
[LICENSE](https://github.com/ORNL-Modelica/DynamicSystemsTestingFramework/blob/main/LICENSE).
