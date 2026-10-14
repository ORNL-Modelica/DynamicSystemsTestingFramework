# Dynamic Systems Testing Framework

DSTF is regression and unit testing for the behaviour of time-dependent
systems. It finds a library's tests, simulates them with a backend — Dymola,
OpenModelica, FMPy, Julia or Python, or one of your own — and scores each
result against a stored baseline by the criteria you write. It reports a
verdict per test on the command line, as JUnit XML and as a dashboard in the
browser.

:::{admonition} Beta
:class: note

DSTF is in beta: the command line and the testing.json keys may still change
between minor versions; the [changelog](changelog.md) says when.
:::

## Install

```console
$ uv tool install dynamic-systems-testing-framework
$ dstf --help
```

`pipx install dynamic-systems-testing-framework` works as well. The package
installs the `dstf` command; [Install and a first run](guides/first-run.md)
takes it from there.

```{toctree}
:caption: Guides
:maxdepth: 1

guides/first-run
guides/testing-json
guides/add-a-test
guides/results
guides/baselines
guides/criteria
guides/ci
guides/simulators
guides/plugin
```

```{toctree}
:caption: Background
:maxdepth: 1

concepts
```

```{toctree}
:caption: Reference
:maxdepth: 1

reference/cli
reference/testing-json
reference/test-spec
reference/comparison-modes
reference/recognizers
reference/api
changelog
```
