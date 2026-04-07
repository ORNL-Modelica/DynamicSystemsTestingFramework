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

    name_matrix = mat['name']
    var_names = _parse_name_matrix(name_matrix)

    data_info = mat.get('dataInfo', None)
    data_1 = mat.get('data_1', None)
    data_2 = mat.get('data_2', None)

    if data_info is None or data_2 is None:
        logger.error("Missing dataInfo or data_2 in %s", mat_path)
        return None

    # dataInfo can be (4, n_vars) or (n_vars, 4) depending on Dymola version.
    # Ensure it's (n_vars, 4) so we can index by variable.
    if data_info.shape[0] < data_info.shape[1]:
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
            val = data_1[col, :]
            if len(val) == 1:
                values = np.full_like(time, val[0])
            else:
                values = val
        elif data_matrix_idx == 2:
            values = data_2[col, :]
        else:
            continue

        if negate:
            values = -values

        result[name] = (time, values)

    return result


def _parse_name_matrix(name_matrix) -> list[str]:
    """Parse Dymola's name matrix into a list of variable name strings.

    Dymola stores variable names as a character matrix (n_vars x max_name_len).
    Depending on scipy version and .mat format, this may come back as:
    - 2D numpy array of uint8/int (character codes)
    - 2D numpy array of single-char strings
    - 1D array of strings (already decoded)
    """
    # If it's a 1D array of strings, we're done
    if name_matrix.ndim == 1:
        return [str(s).strip('\x00').strip() for s in name_matrix]

    # 2D matrix: each row is one variable name
    var_names = []
    for row in name_matrix:
        # Try as character codes first
        try:
            if row.dtype.kind in ('u', 'i', 'f'):
                # Integer/float array — interpret as character codes
                name = ''.join(chr(int(c)) for c in row).strip('\x00').strip()
            else:
                # String/bytes array — join directly
                name = ''.join(str(c) for c in row).strip('\x00').strip()
        except (TypeError, ValueError):
            name = str(row).strip('\x00').strip()
        var_names.append(name)

    return var_names
