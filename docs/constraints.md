# Constraints

## Platform

- **Windows MAX_PATH (260 chars)**: Modelica model paths are deeply nested. Reference filenames derived from model paths easily exceed this limit. Solved with numeric IDs (`ref_0001.json`), but `testing_output/` work directories can still hit this with deep library names + long simulator names.

- **Dymola working directory behavior**: `openModel()` changes Dymola's cwd to the model's directory. Every `.mos` script that writes output must explicitly `cd()` to the desired output directory after any `openModel()` call.

- **Dymola per-simulation artifacts**: `dsin.txt`, `dslog.txt`, `dsfinal.txt`, and `dsres.mat` are all written to cwd. Parallel simulations in the same directory corrupt each other. Per-test subdirectories are mandatory. Artifact names use Dymola defaults (`dsres.mat`, not custom names) to avoid redundancy with the folder name.

## Dymola MAT4 Format

- **Custom binary parser, not scipy**: `mat_reader.py` reads MAT4 files directly using `struct.unpack` for headers and `numpy.memmap` for selective data access. scipy's `loadmat` loads the entire `data_2` matrix into memory, which takes 400+ seconds for large models (76K+ variables). The custom reader extracts only needed rows in under a second.

- **Column-major storage**: MAT4 stores data in Fortran (column-major) order. The name matrix is stored as `(max_name_len, n_vars)` — must be transposed so each row is one variable name. `_read_mat4_block()` uses `order='F'` for correct reshaping.

- **dataInfo matrix orientation**: Sometimes `(n_vars, 4)`, sometimes `(4, n_vars)`. Transposed when `shape[0] < shape[1]`.

- **Data matrix column indexing**: `dataInfo[i, 0]` indicates which data matrix (1 = `data_1` for parameters, 2 = `data_2` for time series). `abs(dataInfo[i, 1]) - 1` is the column index. Sign of `dataInfo[i, 1]` indicates negation. Bounds checking is required — malformed files exist in the wild.

- **data_1 constants**: Dymola stores constants/parameters in `data_1` with 2 columns (start value, end value). The reader uses the first column value and expands to match time length via `np.full_like`.

- **Float32/64 precision**: Older Dymola versions store `.mat` values as float32; newer versions (2024+) may use float64. When float32 is promoted to float64, trailing noise appears (e.g., `0.001` becomes `0.0010000000474974513`). `_to_json_list()` detects the array dtype and rounds to matching precision — 7 significant digits for float32, 15 for float64.

- **Variable name characters in filenames**: Modelica variable names can contain newlines and whitespace (from multi-line expressions like `heatTransfer.alphas[\n        1, 1]`). Plot filenames must sanitize these — `_sanitize_filename()` collapses whitespace, removes filesystem-unsafe characters.

## Simulator Behavior

- **Event handling varies by solver settings**: Dymola's `Evaluate=true` and `storeVariablesAtEvents` flags affect whether duplicate time points appear in results. The comparator handles both cases (with and without events), but reference results should be generated with consistent settings via `simulator_setup` in `testing.json`.

- **Triple time points at events**: Dymola sometimes produces 3 duplicate time points per event (pre-event, intermediate, post-event) rather than the expected 2. The comparator groups consecutive duplicates and uses the first as the segment end and the last as the next segment start, skipping intermediates.

- **Numeric results are platform-dependent**: The same model with the same solver settings produces different floating-point results on Windows vs Linux, and between Dymola vs OpenModelica. References must be partitioned by simulator backend + OS.

- **Translation log capture requires Dymola 2025x+**: `Advanced.UI.TranslationInCommandLog := true;` is the correct flag for Dymola 2025x and newer. Older versions may use `Advanced.TranslationInCommandLog` (without `UI`). The framework hardcodes the 2025x+ syntax.

- **Dymola default output intervals**: When neither `numberOfIntervals` nor `Interval` is specified in the experiment annotation, Dymola defaults to 500 intervals. The framework auto-derives and stores `numberOfIntervals` from the first run to ensure consistent output grids on subsequent runs.

## Stale Artifacts

- **Test directory cleanup is mandatory**: Test directories must be cleaned (`rmtree` + recreate) before each run. Stale `dsres.mat`, `dslog.txt`, or `translation_log.txt` from a previous run can cause false passes if the current simulation fails silently. Translation log is additionally checked for "Translation aborted" as defense in depth.

## Tooling

- **argparse subcommand defaults override parent**: If both parent parser and subcommand define the same argument (e.g., `--reference-root`), the subcommand's default (`None`) silently overwrites the parent's parsed value. Global flags must only be on the parent parser.
