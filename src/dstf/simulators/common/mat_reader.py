"""Reader for DSresult-format MAT files (Dymola / OpenModelica shared output).

Uses a custom MAT4 binary parser with numpy.memmap for selective variable
reading. This avoids loading the entire data_2 matrix (which can be hundreds
of MB for large models) when only a few variables are needed.
"""

import logging
import struct
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# MAT4 data type codes (P field from mopt)
_MAT4_DTYPES = {
    0: np.dtype("<f8"),  # 64-bit float
    1: np.dtype("<f4"),  # 32-bit float
    2: np.dtype("<i4"),  # 32-bit int
    3: np.dtype("<i2"),  # 16-bit int
    4: np.dtype("<u2"),  # 16-bit unsigned
    5: np.dtype("<u1"),  # 8-bit unsigned
}


def _scan_mat4_headers(f) -> dict:
    """Scan a MAT4 file and return metadata for each top-level variable.

    Returns dict of {name: (data_offset, dtype, mrows, ncols)}.
    Only reads headers + variable names, skips all data payloads via seek().
    """
    variables = {}
    while True:
        hdr_bytes = f.read(20)
        if len(hdr_bytes) < 20:
            break

        mopt, mrows, ncols, imagf, namlen = struct.unpack("<5i", hdr_bytes)

        # Extract data type from mopt: P = (mopt % 100) // 10
        p_code = (mopt % 100) // 10
        dtype = _MAT4_DTYPES.get(p_code, np.dtype("<f8"))

        # Read variable name
        name_bytes = f.read(namlen)
        name = name_bytes.rstrip(b"\x00").decode("latin1")

        data_offset = f.tell()
        data_size = mrows * ncols * dtype.itemsize

        variables[name] = (data_offset, dtype, mrows, ncols)

        # Skip past the data payload
        f.seek(data_offset + data_size)

    return variables


def _read_mat4_block(f, info) -> np.ndarray:
    """Read a full MAT4 data block into a numpy array.

    Data is stored column-major (Fortran order), so a (mrows, ncols) matrix
    is stored as ncols consecutive columns of mrows elements each.
    Returns array with shape (mrows, ncols).
    """
    data_offset, dtype, mrows, ncols = info
    f.seek(data_offset)
    raw = f.read(mrows * ncols * dtype.itemsize)
    return np.frombuffer(raw, dtype=dtype).reshape((mrows, ncols), order="F")


def list_result_mat_variables(mat_path: Path) -> list[str] | None:
    """Read only the variable names from a Dymola .mat file (no data loaded).

    This is fast even for large files — it reads headers and the name matrix only.
    """
    try:
        with open(mat_path, "rb") as f:
            blocks = _scan_mat4_headers(f)

            if "name" not in blocks:
                logger.error("No 'name' matrix in %s", mat_path)
                return None

            # Dymola stores names as (max_name_len, n_vars) — transpose to
            # get (n_vars, max_name_len) so each row is one variable name.
            name_matrix = _read_mat4_block(f, blocks["name"]).T
            return _parse_name_matrix(name_matrix)
    except Exception as e:
        logger.error("Failed to read %s: %s", mat_path, e)
        return None


def read_result_mat(
    mat_path: Path,
    variable_names: set[str] | None = None,
) -> dict | None:
    """Read a Dymola-format .mat file with selective variable loading.

    Dymola uses a specific MAT4 format with:
    - 'Aclass': metadata matrix
    - 'name': character matrix of variable names
    - 'data_1': parameter/constant values
    - 'data_2': trajectory values (time-varying)
    - 'dataInfo': mapping from name index to data matrix and column

    Args:
        mat_path: Path to the .mat file.
        variable_names: If provided, only extract these variables.
            Uses numpy.memmap on data_2 to read only the needed rows,
            avoiding loading the entire trajectory matrix into memory.

    Returns dict with variable names as keys and (time, values) tuples.
    """
    try:
        with open(mat_path, "rb") as f:
            blocks = _scan_mat4_headers(f)

            if "name" not in blocks:
                logger.error("No 'name' matrix in %s", mat_path)
                return None

            if "dataInfo" not in blocks or "data_2" not in blocks:
                logger.error("Missing dataInfo or data_2 in %s", mat_path)
                return None

            # Load small matrices eagerly.
            # Dymola stores names as (max_name_len, n_vars) — transpose so
            # each row is one variable name.
            name_matrix = _read_mat4_block(f, blocks["name"]).T
            data_info = _read_mat4_block(f, blocks["dataInfo"])

            data_1 = None
            if "data_1" in blocks:
                data_1 = _read_mat4_block(f, blocks["data_1"])

        all_var_names = _parse_name_matrix(name_matrix)

        # MAT4 stores dataInfo column-major as (4, n_vars) — each column is one
        # variable's {data_matrix, col, interp, protected} quad. We want to
        # index by variable, so transpose to (n_vars, 4). The 4-row invariant
        # is fixed by the DSresult format; an earlier shape[0]<shape[1]
        # heuristic silently broke for MATs with n_vars <= 4 (only surfaced
        # on OM where tight variableFilter often leaves a handful of vars).
        if (
            data_info.ndim == 2
            and data_info.shape[0] == 4
            or data_info.ndim == 2
            and data_info.shape[0] < data_info.shape[1]
        ):
            data_info = data_info.T

        # Memory-map data_2 for selective row access.
        # MAT4 is column-major: Fortran order gives correct (mrows, ncols) indexing.
        # For Dymola, mrows = n_trajectory_vars, ncols = n_timesteps (but transposed
        # from MATLAB convention). Dymola stores data_2 as (n_timesteps, n_vars) in
        # MATLAB convention, so mrows=n_timesteps, ncols=n_vars.
        d2_offset, d2_dtype, d2_mrows, d2_ncols = blocks["data_2"]
        data_2 = np.memmap(
            mat_path,
            dtype=d2_dtype,
            mode="r",
            offset=d2_offset,
            shape=(d2_mrows, d2_ncols),
            order="F",
        )

        time = np.array(data_2[0, :])  # Copy time out of memmap

        # Determine which rows we actually need to read
        needed_rows = set()
        if variable_names is not None:
            for i, name in enumerate(all_var_names):
                if i >= len(data_info):
                    break
                if name not in variable_names:
                    continue
                info = data_info[i]
                data_matrix_idx = int(info[0])
                col = abs(int(info[1])) - 1
                if data_matrix_idx == 2:
                    needed_rows.add(col)

        result = {}
        for i, name in enumerate(all_var_names):
            if i >= len(data_info):
                break

            # Skip variables we don't need
            if variable_names is not None and name not in variable_names:
                continue

            info = data_info[i]
            data_matrix_idx = int(info[0])
            col_idx = int(info[1])
            negate = col_idx < 0
            col = abs(col_idx) - 1

            if data_matrix_idx == 1 and data_1 is not None:
                if col >= data_1.shape[0]:
                    continue
                # data_1 stores constants/parameters. Dymola typically uses
                # 2 columns (start_value, end_value). Use the first value
                # and expand to match time length.
                val = data_1[col, 0] if data_1.ndim == 2 else data_1[col]
                values = np.full_like(time, float(val))
            elif data_matrix_idx == 2:
                if col >= data_2.shape[0]:
                    continue
                # Only this row is actually read from disk (memmap)
                values = np.array(data_2[col, :])
            else:
                continue

            if negate:
                values = -values

            result[name] = (time, values)

        return result

    except Exception as e:
        logger.error("Failed to read %s: %s", mat_path, e)
        return None


def read_mat_time_extents(mat_path: Path) -> tuple[float, float] | None:
    """Cheaply read just the first and last time values from a Dymola .mat.

    Useful for verifying a simulation reached its requested stop time
    (a partial / killed simulation can leave a valid-looking .mat with
    data only up to where it stopped). Returns None if the file is
    unreadable or contains no time data.

    Bypasses the full variable-iteration in read_dymola_mat — Dymola stores
    time as row 0 of data_2, so we only need that single row from disk.
    """
    try:
        with open(mat_path, "rb") as f:
            blocks = _scan_mat4_headers(f)
        if "data_2" not in blocks:
            return None
        d2_offset, d2_dtype, d2_mrows, d2_ncols = blocks["data_2"]
        # Map only enough to read row 0 (time) — column-major Fortran order
        data_2 = np.memmap(
            mat_path,
            dtype=d2_dtype,
            mode="r",
            offset=d2_offset,
            shape=(d2_mrows, d2_ncols),
            order="F",
        )
        if d2_mrows == 0 or d2_ncols == 0:
            return None
        return float(data_2[0, 0]), float(data_2[0, -1])
    except Exception:
        return None


def _parse_name_matrix(name_matrix: np.ndarray) -> list[str]:
    """Parse Dymola's name matrix into a list of variable name strings.

    Dymola stores names as a 2D integer array (n_vars x max_name_len)
    where each row contains character codes for one variable name.
    """
    if name_matrix.ndim == 2 and name_matrix.dtype.kind in ("u", "i", "f"):
        # 2D integer/float array — each row is char codes for one name
        var_names = []
        for row in name_matrix:
            name = "".join(chr(int(c)) for c in row).strip("\x00").strip()
            var_names.append(name)
        return var_names

    if name_matrix.ndim == 1 and name_matrix.dtype.kind == "U":
        # 1D array of unicode strings — column-major character matrix
        n_cols = len(name_matrix)
        if n_cols == 0:
            return []
        n_vars = len(name_matrix[0])
        var_names = []
        for j in range(n_vars):
            name = "".join(
                name_matrix[i][j] for i in range(n_cols) if j < len(name_matrix[i])
            )
            name = name.strip("\x00").strip()
            if name:
                var_names.append(name)
        return var_names

    if name_matrix.ndim == 2 and name_matrix.dtype.kind in ("U", "S"):
        # 2D array of single-char strings — each row is one name
        var_names = []
        for row in name_matrix:
            name = "".join(str(c) for c in row).strip("\x00").strip()
            var_names.append(name)
        return var_names

    # Fallback
    logger.warning(
        "Unexpected name matrix format: dtype=%s shape=%s",
        name_matrix.dtype,
        name_matrix.shape,
    )
    var_names = []
    for item in name_matrix.flat:
        var_names.append(str(item).strip("\x00").strip())
    return var_names
