# Constraints

## Platform

- **Windows MAX_PATH (260 chars)**: Modelica model paths are deeply nested. Reference filenames derived from model paths easily exceed this limit. Solved with numeric IDs (`ref_0001.json`), but `testing_output/` work directories can still hit this with deep library names + long simulator names.

- **Dymola working directory behavior**: `openModel()` changes Dymola's cwd to the model's directory. Every `.mos` script that writes output must explicitly `cd()` to the desired output directory after any `openModel()` call.

- **Dymola per-simulation artifacts**: `dsin.txt`, `dslog.txt`, `dsfinal.txt`, and the `.mat` result file are all written to cwd. Parallel simulations in the same directory corrupt each other. Per-test subdirectories are mandatory.

## Dymola MAT4 Format

- **scipy returns column-major name matrix**: The `name` matrix in `.mat` files can come back as 1D array of unicode strings (each string is one column of the original char matrix), 2D uint8 array, or 2D char array. `mat_reader.py` handles all three cases via `_parse_name_matrix()`.

- **dataInfo matrix orientation**: Sometimes `(n_vars, 4)`, sometimes `(4, n_vars)`. Transposed when `shape[0] < shape[1]`.

- **Data matrix column indexing**: `dataInfo[i, 0]` indicates which data matrix (1 = `data_1` for parameters, 2 = `data_2` for time series). `abs(dataInfo[i, 1]) - 1` is the column index. Sign of `dataInfo[i, 1]` indicates interpolation order. Bounds checking is required — malformed files exist in the wild.

## Simulator Behavior

- **Event handling varies by solver settings**: Dymola's `Evaluate=true` and `storeVariablesAtEvents` flags affect whether duplicate time points appear in results. The comparator handles both cases (with and without events), but reference results should be generated with consistent settings via `simulator_setup` in `testing.json`.

- **Numeric results are platform-dependent**: The same model with the same solver settings produces different floating-point results on Windows vs Linux, and between Dymola vs OpenModelica. References must be partitioned by simulator backend + OS.

## Tooling

- **argparse subcommand defaults override parent**: If both parent parser and subcommand define the same argument (e.g., `--reference-root`), the subcommand's default (`None`) silently overwrites the parent's parsed value. Global flags must only be on the parent parser.
