# Python

The Python backend runs tests that are Python functions. Each test is a `.py`
file with a function

```python
def simulate(stop_time: float, tolerance: float) -> dict:
    return {
        "time": [...],               # increasing times
        "variables": {"x": [...]},   # one list per variable, as long as time
    }
```

Anything that returns that dict is a test: a SciPy integration, a recorded
CSV file, a call to a service. `stop_time` and `tolerance` are the test's
`simulation` settings. `examples/python/PythonTestingLib` in the repository
holds three; [Install and a first run](../first-run.md) runs them.

A project of Python tests sets {ref}`source_type <key-testing-source-type>` to
`"python"` and names each test's file in its entry's `source`.

## Which Python runs the tests

Each test runs in a Python process of its own, started with the executable
the Python block names — so that Python needs what the tests import. Without
a block, it is the Python dstf itself runs in, which for a `uv tool` or
`pipx` install has only dstf's own packages. Name the Python that has your
tests' packages:

<!-- snippet: testing -->
```json
{
  "source_type": "python",
  "source_path": "../..",
  "backends": {
    "Python": {"executables": ["python3"]}
  }
}
```

`python3` is looked up on `PATH`, so an activated virtual environment's is
taken; a path pins one Python.

## Check

When it is found:

```text
Python
  executable: /home/you/project/.venv/bin/python3
    found by: testing.json: backends.Python.executables

OK — Python is ready.
```

When the block names a Python that is not there:

```text
Python
  executable: not found — the configured executables: /opt/python3.12/bin/python3 does not exist

FAIL — Python is not ready. Fix one of:
  1. Install Python, or set "executables" in its block of testing.json's "backends" to where it is
```

## Settings

The Python block takes `executables` alone: {ref}`backend-python`.
