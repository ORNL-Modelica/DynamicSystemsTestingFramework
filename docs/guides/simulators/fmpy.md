# FMPy

FMPy simulates FMUs inside dstf's own Python. It is the
`fmpy` extra:

```console
$ uv tool install "dynamic-systems-testing-framework[fmpy]"
```

A project of FMUs sets {ref}`source_type <key-testing-source-type>` to `"fmu"`;
each test is an entry of `test_spec.json` whose `source` is its `.fmu`:

<!-- snippet: testing -->
```json
{
  "source_type": "fmu",
  "source_path": ".",
  "backend": "FMPy"
}
```

<!-- snippet: test_spec -->
```json
{
  "tests": [
    {
      "model": "BouncingBall",
      "source": "fmus/BouncingBall.fmu",
      "variables": ["h", "v"],
      "simulation": {"stop_time": 3.0}
    }
  ]
}
```

`examples/fmu/` in the repository runs the Modelica Association's Reference
FMUs; `scripts/fetch_reference_fmus.py` downloads them.

## Check

FMPy is found when `fmpy` can be imported by the Python dstf runs in:

```text
FMPy
  fmpy package: /home/you/.local/share/uv/tools/dynamic-systems-testing-framework/lib/python3.12/site-packages/fmpy
    found by: search: this Python's packages
    version 0.3.29

OK — FMPy is ready.
```

When it cannot be:

```text
FMPy
  fmpy package: not found — this Python has no fmpy; install the [fmpy] extra

FAIL — FMPy is not ready. Fix one of:
  1. Install fmpy: reinstall dstf with the [fmpy] extra (from a checkout: uv sync --extra fmpy --locked --inexact)
```

## Settings

FMPy takes no settings: {ref}`backend-fmpy`. Its block, when there is one, is
empty.
