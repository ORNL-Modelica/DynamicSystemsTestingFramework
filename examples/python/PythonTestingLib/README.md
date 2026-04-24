# PythonTestingLib

Example Python-source test library for the `dstf` framework.

## Setup

Install the Python dependencies that the examples import (scipy is
used by `SimpleRamp.py`; the `ConstantCsv.py` example has no deps
beyond the standard library):

```bash
cd examples/python/PythonTestingLib
uv pip install -e .
```

## Run

```bash
uv run dstf \
    --config examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json \
    run
```

## Test contract

Each `.py` file under `Examples/` must define a top-level function:

```python
def simulate(stop_time: float, tolerance: float) -> dict:
    return {
        "time": [...],               # list[float]
        "variables": {"x": [...]},   # dict[str, list[float]]
    }
```

`stop_time` and `tolerance` come from the matching `simulation` block
in `Resources/ReferenceResults/test_spec.json`. Return whatever
makes sense — an scipy ODE solution, pre-recorded CSV data, results
from a REST call, etc. The framework's only requirement is that
`time` is monotonically non-decreasing and each variable list has
the same length as `time`.
