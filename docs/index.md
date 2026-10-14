# Dynamic Systems Testing Framework

DSTF is regression and unit testing for the behaviour of time-dependent
systems. It finds a library's tests, simulates them with a backend — Dymola,
OpenModelica, FMPy, Julia or Python, or one of your own — and scores each
result against a stored baseline by the criteria you write. It reports a
verdict per test on the command line, as JUnit XML and as a dashboard in the
browser.

## Install

```console
$ pip install dynamic-systems-testing-framework
$ dstf --help
```

The package installs the `dstf` command. `dstf author`, the browser tool for
writing criteria, needs the `author` extra:
`pip install "dynamic-systems-testing-framework[author]"`.

## Reference

```{toctree}
:maxdepth: 2

reference/cli
reference/testing-json
reference/test-spec
reference/comparison-modes
reference/recognizers
reference/api
```
