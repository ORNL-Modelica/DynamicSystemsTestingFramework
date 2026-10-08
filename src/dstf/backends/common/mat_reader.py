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


def _raise_if_binnormal(f, blocks, mat_path: Path) -> None:
    """Refuse binNormal-layout DSresult files.

    review 2026-07-06 (finding 74): DSresult MATs come in two layouts —
    binTrans (the Dymola/OM default; matrices stored transposed) declared in
    the Aclass matrix, and binNormal. This reader only decodes binTrans; a
    binNormal file (user-supplied .mat baseline) previously decoded as
    transposed garbage silently returned as success. The raise is caught by
    the callers' blanket handler and surfaced as a clear per-file error.
    """
    if "Aclass" not in blocks:
        return  # no metadata — assume binTrans (historic behavior)
    aclass = _read_mat4_block(f, blocks["Aclass"])
    try:
        text_f = "".join(chr(int(c)) for c in np.asarray(aclass).flatten(order="F"))
        text_c = "".join(chr(int(c)) for c in np.asarray(aclass).flatten(order="C"))
    except (ValueError, OverflowError, TypeError):
        return  # unreadable Aclass — don't block the historic path
    if "binNormal" in text_f or "binNormal" in text_c:
        raise ValueError(
            f"{mat_path}: binNormal .mat layout not supported — "
            f"re-export with Dymola/OM default binTrans"
        )


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

            # review 2026-07-06 (finding 74): binNormal names are stored
            # untransposed — decoding them with the binTrans transpose below
            # yields garbage names.
            _raise_if_binnormal(f, blocks, mat_path)

            # Dymola stores names as (max_name_len, n_vars) — transpose to
            # get (n_vars, max_name_len) so each row is one variable name.
            name_matrix = _read_mat4_block(f, blocks["name"]).T
            return _parse_name_matrix(name_matrix)
    except Exception as e:
        logger.error("Failed to read %s: %s", mat_path, e)
        return None


def _read_tables(f, blocks, mat_path: Path):
    """The header matrices a trajectory is read through: every variable's
    name, its ``dataInfo`` entry as one row ``(matrix, column, interpolation,
    protected)``, and ``data_1`` (``None`` when the file has none).
    """
    # review 2026-07-06 (finding 74): binNormal files would decode
    # as transposed garbage returned as success. Refuse loudly.
    _raise_if_binnormal(f, blocks, mat_path)

    # Dymola stores names as (max_name_len, n_vars) — transpose so
    # each row is one variable name.
    name_matrix = _read_mat4_block(f, blocks["name"]).T
    data_info = _read_mat4_block(f, blocks["dataInfo"])

    data_1 = None
    if "data_1" in blocks:
        data_1 = _read_mat4_block(f, blocks["data_1"])

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

    return _parse_name_matrix(name_matrix), data_info, data_1


def _variable_sources(
    names: list[str],
    data_info: np.ndarray,
    data_1: np.ndarray | None,
    n_trajectories: int,
    mat_path: Path,
    wanted: set[str] | None = None,
):
    """Where each readable variable's values are: yields ``(name, row,
    negate, constant)`` — ``row`` of ``data_2`` for a trajectory, or
    ``constant`` (``row`` is then ``None``) for a parameter of ``data_1``;
    ``negate`` for an alias stored with the opposite sign. A variable the
    file gives no data for is left out, as is one not in ``wanted``.
    """
    for i, name in enumerate(names):
        if i >= len(data_info):
            break

        # Skip variables we don't need
        if wanted is not None and name not in wanted:
            continue

        info = data_info[i]
        data_matrix_idx = int(info[0])
        col_idx = int(info[1])
        if col_idx == 0:
            # review 2026-07-06 (finding 74): dataInfo column 0 means "not
            # stored in a data matrix" (abscissa marker); abs(0)-1 == -1
            # previously wrapped to the LAST row — returning the wrong
            # variable's data under the requested name.
            logger.debug(
                "Variable %s has dataInfo column 0 in %s; skipping",
                name,
                mat_path,
            )
            continue
        negate = col_idx < 0
        col = abs(col_idx) - 1

        if data_matrix_idx == 1 and data_1 is not None:
            if col >= data_1.shape[0]:
                continue
            # data_1 stores constants/parameters. Dymola typically uses
            # 2 columns (start_value, end_value). Use the first value.
            val = data_1[col, 0] if data_1.ndim == 2 else data_1[col]
            yield name, None, negate, float(val)
        elif data_matrix_idx == 2:
            if col >= n_trajectories:
                continue
            yield name, col, negate, None


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
            all_var_names, data_info, data_1 = _read_tables(f, blocks, mat_path)

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

        result = {}
        for name, row, negate, constant in _variable_sources(
            all_var_names, data_info, data_1, d2_mrows, mat_path, variable_names
        ):
            if row is None:
                # A parameter: its one value at every time point.
                values = np.full_like(time, constant)
            else:
                # Only this row is actually read from disk (memmap)
                values = np.array(data_2[row, :])

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
            # review 2026-07-06 (finding 74) applies here too: in a binNormal
            # file data_2 is stored untransposed, so row 0 is not time — the
            # extents would be garbage. The raise lands in the handler below.
            _raise_if_binnormal(f, blocks, mat_path)
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


def mat_reaches_time(
    mat_path: Path,
    target_time: float,
) -> tuple[bool, float | None]:
    """Did the trajectory in ``mat_path`` reach ``target_time``?

    Returns ``(reached, last_time)``; ``last_time`` is None when the file
    is unreadable. The relative tolerance mirrors the persistent-OM
    disk-rescue rule so success-path and rescue-path verdicts can't
    disagree about the same file (D99).
    """
    extents = read_mat_time_extents(mat_path)
    if extents is None:
        return False, None
    last_time = extents[1]
    tol = max(1e-6, abs(target_time) * 1e-6)
    return last_time + tol >= target_time, last_time


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
