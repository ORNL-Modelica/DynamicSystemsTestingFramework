# Investigation: .mat File Read Performance

**Status**: Partially resolved — custom MAT4 reader implemented, but root cause of original hang not fully verified.

## Problem

When running 60 TRANSFORM.Fluid tests with `--parallel 10`, the process appeared to hang during the "Reading result files" phase. Progress stopped at 58/60 with the last two tests (HumTest and PbLiTest2) never completing.

## What We Know

1. **File sizes**: HumTest produces a 36MB .mat file with ~76,992 variables. PbLiTest2 produces a 24MB .mat file.
2. **Old reader timing**: `scipy.io.loadmat` on the 36MB file took **397 seconds** (6.6 minutes) in a standalone test. This is because `loadmat` reads the entire `data_2` trajectory matrix into memory — all 76,992 variable rows — even though we only need ~10.
3. **Custom reader**: Replaced `scipy.io.loadmat` with a direct MAT4 binary parser using `numpy.memmap`. This reads only the needed variable rows from `data_2`, which should reduce the 397s to under 1 second.
4. **scipy dropped**: The project no longer depends on scipy.

## What We Haven't Verified

- **Was `loadmat` actually the cause of the hang?** The 397s measurement was from a standalone timing test, not from the actual parallel run. The hang could have been caused by:
  - Thread pool deadlock (GIL contention with large numpy operations across threads)
  - WSL2 9P filesystem I/O contention (multiple large reads on /mnt/d/ simultaneously)
  - Memory pressure from loading multiple large files in parallel
  - Something else entirely (comparison step, print lock, etc.)

- **Does the custom reader fix the hang in practice?** The user reports reads are "much much faster" now, but we haven't reproduced the exact hang scenario to confirm it's resolved.

## How to Verify

### 1. Run a single large test without parallelization

```bash
uv run python -m modelica_testing --reference-root ..\TRANSFORM-UnitTests\ReferenceResults run --package TRANSFORM.Fluid.Examples.HumTest --parallel 1
```

If this completes quickly, the reader itself is fine. If it hangs, the issue is in reading/comparison, not parallelization.

### 2. Run with verbose logging

```bash
uv run python -m modelica_testing --reference-root ..\TRANSFORM-UnitTests\ReferenceResults run --package TRANSFORM.Fluid --parallel 10 --log-level debug 2>debug.log
```

Check `debug.log` for timing of individual operations.

### 3. Run the full 60-test suite and watch for hangs

```bash
uv run python -m modelica_testing --reference-root ..\TRANSFORM-UnitTests\ReferenceResults run --package TRANSFORM.Fluid --parallel 10
```

If the read phase completes within ~30 seconds (vs. the previous hang at 58/60), the custom reader fixed it.

### 4. If the hang recurs, add targeted timing

Add `time.monotonic()` calls in `DymolaRunner.read_result()` around:
- `list_dymola_mat_variables()` — should be <100ms even for large files
- `read_dymola_mat()` with selective loading — should be <1s
- `_extract_variables()` — should be instant

This would pinpoint whether the bottleneck is I/O, parsing, or downstream.

## Changes Made

| File | Change |
|------|--------|
| `mat_reader.py` | Replaced `scipy.io.loadmat` with custom MAT4 binary parser using `numpy.memmap` for selective row reads |
| `mat_reader.py` | Added `list_dymola_mat_variables()` for fast name-only reads |
| `runner.py` | Added `_compute_needed_variables()` to pre-compute which variables to extract |
| `runner.py` | `read_result()` now does two-phase loading: names first, then selective data |
| `pyproject.toml` | Removed `scipy>=1.7` dependency |

## Related

- The original `scipy.io.loadmat` call loaded ALL top-level MAT variables (`data_2` is the large one). DyMat and BuildingsPy both use the same `loadmat` call internally, so they have identical performance.
- The MAT4 format is simple (20-byte headers + raw data), making a custom reader straightforward (~100 lines).
