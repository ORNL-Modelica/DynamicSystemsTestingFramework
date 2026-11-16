# PythonTestingLib

The Python-backend example library: each `.py` file under `Examples/`
defines `simulate(stop_time, tolerance) -> dict`, and
`Resources/ReferenceResults/` holds its `testing.json`, `test_spec.json`
and baselines. `SimpleRamp` and `PointsCheck` need numpy and scipy (the
`dev` extra installs both).

From the repository root:

```bash
uv run dstf --config examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json run --no-browser
```

A first run over it, step by step: `docs/guides/first-run.md`. What a
Python test returns and how it is run: `docs/guides/simulators/python.md`.
