"""Read Dymola .mat (MAT4) result files."""

import logging
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


def read_dymola_mat(mat_path: Path) -> Optional[dict]:
    """Read a Dymola-format .mat file.

    Dymola uses a specific MAT4 format with:
    - 'Aclass': metadata matrix
    - 'name': character matrix of variable names
    - 'data_1': parameter/constant values
    - 'data_2': trajectory values (time-varying)
    - 'dataInfo': mapping from name index to data matrix and column

    Returns dict with variable names as keys and (time, values) tuples.
    """
    try:
        from scipy.io import loadmat
    except ImportError:
        raise ImportError("scipy is required for reading .mat files: pip install scipy")

    try:
        mat = loadmat(str(mat_path), squeeze_me=False)
    except Exception as e:
        logger.error("Failed to read %s: %s", mat_path, e)
        return None

    if 'name' not in mat:
        logger.error("No 'name' matrix in %s", mat_path)
        return None

    var_names = _parse_name_matrix(mat['name'])

    data_info = mat.get('dataInfo', None)
    data_1 = mat.get('data_1', None)
    data_2 = mat.get('data_2', None)

    if data_info is None or data_2 is None:
        logger.error("Missing dataInfo or data_2 in %s", mat_path)
        return None

    # dataInfo can be (4, n_vars) or (n_vars, 4) depending on storage order.
    # We need (n_vars, 4) so we can index by variable.
    if data_info.ndim == 2 and data_info.shape[0] < data_info.shape[1]:
        data_info = data_info.T

    time = data_2[0, :]

    result = {}
    for i, name in enumerate(var_names):
        if i >= len(data_info):
            break
        info = data_info[i]
        data_matrix_idx = int(info[0])
        col_idx = int(info[1])
        negate = col_idx < 0
        col = abs(col_idx) - 1

        if data_matrix_idx == 1 and data_1 is not None:
            if col >= data_1.shape[0]:
                continue
            val = data_1[col, :]
            if len(val) == 1:
                values = np.full_like(time, val[0])
            else:
                values = val
        elif data_matrix_idx == 2:
            if col >= data_2.shape[0]:
                continue
            values = data_2[col, :]
        else:
            continue

        if negate:
            values = -values

        result[name] = (time, values)

    return result


def _parse_name_matrix(name_matrix) -> list[str]:
    """Parse Dymola's name matrix into a list of variable name strings.

    Dymola stores names as a character matrix. Depending on scipy version
    and .mat format version, this can come back in several forms:

    Case 1: 2D array of uint8 (n_vars x max_name_len) — each row is a name
    Case 2: 1D array of strings where each string is a COLUMN of the original
            character matrix (column-major storage). Names are reconstructed
            by reading the j-th character from each string.
    Case 3: 2D array of single-char strings
    """
    if name_matrix.ndim == 2 and name_matrix.dtype.kind in ('u', 'i', 'f'):
        # Case 1: 2D integer array — each row is char codes for one name
        var_names = []
        for row in name_matrix:
            name = ''.join(chr(int(c)) for c in row).strip('\x00').strip()
            var_names.append(name)
        return var_names

    if name_matrix.ndim == 1 and name_matrix.dtype.kind == 'U':
        # Case 2: 1D array of unicode strings — column-major character matrix
        # Each element is one column of the matrix: element[i] has one char
        # per variable. Reconstruct names by reading across elements.
        n_cols = len(name_matrix)
        if n_cols == 0:
            return []
        n_vars = len(name_matrix[0])  # Each string has n_vars characters
        var_names = []
        for j in range(n_vars):
            name = ''.join(name_matrix[i][j] for i in range(n_cols) if j < len(name_matrix[i]))
            name = name.strip('\x00').strip()
            if name:
                var_names.append(name)
        return var_names

    if name_matrix.ndim == 2 and name_matrix.dtype.kind in ('U', 'S'):
        # Case 3: 2D array of single-char strings — each row is one name
        var_names = []
        for row in name_matrix:
            name = ''.join(str(c) for c in row).strip('\x00').strip()
            var_names.append(name)
        return var_names

    # Fallback: try to interpret as strings
    logger.warning("Unexpected name matrix format: dtype=%s shape=%s", name_matrix.dtype, name_matrix.shape)
    var_names = []
    for item in name_matrix.flat:
        var_names.append(str(item).strip('\x00').strip())
    return var_names
