"""Reader for DSresult-format MAT files (Dymola / OpenModelica shared output).

Uses a custom MAT4 binary parser with numpy.memmap for selective variable
reading. This avoids loading the entire data_2 matrix (which can be hundreds
of MB for large models) when only a few variables are needed.

**How many time points a file holds comes from its SIZE, never from the
count in its header.** The trajectory matrix ``data_2`` is the file's last
matrix and both tools write its header BEFORE its rows: Dymola's count
starts at 1 and lags the rows by tens to hundreds of them while a run is in
progress, OpenModelica's stays 0 until the run ends. Neither is completed
when a run is killed. So the reader takes the whole time points the file
holds — a file cut mid-point yields the points before the cut — and a
crashed or timed-out run's rows are readable to where they stop.

Readable is not finished. A VERDICT (:func:`read_mat_time_extents`,
:func:`mat_reaches_time`) and a scored read (:func:`mat_shortfall`) still
ask for a finished file, and what says a file is finished is the header's
own count: it equals the points present and nothing follows them. A partial
result is shown, never scored and never rescued as a pass.

The anchor for "``data_2`` is last": every result both tools were seen to
write (Dymola 2026x, omc 1.27) ends with it, with no byte after its last
point. A writer that appended a matrix after ``data_2`` would make this
reader count that matrix's bytes as time points; such a file is then not
"finished" by the rule above (its header counts fewer), so it would fail
loudly rather than score.
"""

import logging
import os
import struct
from collections.abc import Collection
from pathlib import Path
from typing import NamedTuple

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


#: The trajectory matrix: the one whose column count is taken from the size.
_TRAJECTORY = "data_2"


class _Scan(NamedTuple):
    """What a file's headers and its size say it holds."""

    #: name → (data_offset, dtype, mrows, ncols) of every matrix the file
    #: holds whole, and of the trajectory with the columns PRESENT.
    blocks: dict
    #: The trajectory header's own column count; ``None`` without one.
    declared: int | None
    #: Bytes after the trajectory's last whole column.
    trailing: int

    @property
    def finished(self) -> bool:
        """Did its writer finish this file: a trajectory of at least one
        time point, as many as its header counts, and nothing after."""
        block = self.blocks.get(_TRAJECTORY)
        return (
            block is not None
            and block[3] > 0
            and block[3] == self.declared
            and self.trailing == 0
        )


def _scan_mat4_headers(f) -> _Scan:
    """Scan a MAT4 file and return metadata for each top-level variable.

    Only reads headers + variable names, skips all data payloads via seek().

    The trajectory's column count — its time points — is worked out from
    the file's size: the whole columns present (see the module docstring).
    The scan stops there, and at the first matrix before it that the file
    does not hold whole yet; a caller missing a matrix it needs then has
    nothing to read.
    """
    size = os.fstat(f.fileno()).st_size
    variables = {}
    while True:
        hdr_bytes = f.read(20)
        if len(hdr_bytes) < 20:
            break

        mopt, mrows, ncols, imagf, namlen = struct.unpack("<5i", hdr_bytes)
        if namlen <= 0 or mrows < 0 or ncols < 0:
            break  # not a header: a cut or foreign file

        # Extract data type from mopt: P = (mopt % 100) // 10
        p_code = (mopt % 100) // 10
        dtype = _MAT4_DTYPES.get(p_code, np.dtype("<f8"))

        # Read variable name
        name_bytes = f.read(namlen)
        if len(name_bytes) < namlen:
            break
        name = name_bytes.rstrip(b"\x00").decode("latin1")

        data_offset = f.tell()

        if name == _TRAJECTORY:
            col_bytes = mrows * dtype.itemsize
            payload = max(0, size - data_offset)
            present = payload // col_bytes if col_bytes else 0
            variables[name] = (data_offset, dtype, mrows, present)
            return _Scan(variables, ncols, payload - present * col_bytes)

        data_size = mrows * ncols * dtype.itemsize
        if data_offset + data_size > size:
            break  # this matrix is not all there yet

        variables[name] = (data_offset, dtype, mrows, ncols)

        # Skip past the data payload
        f.seek(data_offset + data_size)

    return _Scan(variables, None, 0)


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
            blocks = _scan_mat4_headers(f).blocks

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


class ResultMatFile:
    """A result file's header matrices, parsed ONCE, and any of its
    variables read through them after that: :meth:`series` as often as
    asked costs one parse — a quarter of a second for 20,000 variables —
    and every series it hands out shares one time array.

    For a file that no longer changes (a finished result, or what a run
    that was stopped left). One that does change between two reads — its
    identity, size or modification time — is parsed again, so a file
    replaced under the path is never read through the header of the one
    before.
    """

    def __init__(self, mat_path: Path) -> None:
        self.path = Path(mat_path)
        self._stamp: tuple[int, int, int, int] | None = None
        #: The trajectory's ``(offset, dtype, mrows, ncols)``.
        self._block: tuple | None = None
        #: name → (row of ``data_2`` or None, negate, constant), in the
        #: file's order (:func:`_variable_sources`).
        self._sources: dict[str, tuple[int | None, bool, float | None]] = {}
        self._order: dict[str, int] = {}
        self._names: tuple[str, ...] = ()
        self._time: np.ndarray | None = None

    @property
    def names(self) -> tuple[str, ...] | None:
        """Every variable the file names, in its order, the abscissa left
        out — what :func:`list_result_mat_variables` reads of the same file
        (``result_files.mat_names``), from the one parse. None for a file
        that cannot be read."""
        return None if self.series(()) is None else self._names

    @staticmethod
    def _stamp_of(st: os.stat_result) -> tuple[int, int, int, int]:
        return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns

    def _parse(self) -> bool:
        """Read the header matrices; False (logged) for a file without
        them."""
        self._block, self._sources, self._order, self._time = None, {}, {}, None
        self._names = ()
        with open(self.path, "rb") as f:
            self._stamp = self._stamp_of(os.fstat(f.fileno()))
            blocks = _scan_mat4_headers(f).blocks

            if "name" not in blocks:
                logger.error("No 'name' matrix in %s", self.path)
                return False

            if "dataInfo" not in blocks or "data_2" not in blocks:
                logger.error("Missing dataInfo or data_2 in %s", self.path)
                return False

            # Load small matrices eagerly.
            all_var_names, data_info, data_1 = _read_tables(f, blocks, self.path)

        block = blocks["data_2"]
        for name, row, negate, constant in _variable_sources(
            all_var_names, data_info, data_1, block[2], self.path
        ):
            self._sources[name] = (row, negate, constant)
        self._order = {name: i for i, name in enumerate(self._sources)}
        self._names = tuple(n for n in all_var_names if n.lower() != "time")
        self._block = block
        return True

    def series(self, variable_names: Collection[str] | None = None) -> dict | None:
        """``{name: (time, values)}`` for ``variable_names`` (every
        variable for None), in the file's order — what
        :func:`read_result_mat` returns, which is this on a file opened for
        the one read. None, with the reason logged, for a file that cannot
        be read."""
        try:
            changed = self._stamp is None or self._stamp != self._stamp_of(
                os.stat(self.path)
            )
            if changed and not self._parse():
                return None
            if self._block is None:
                return None  # parsed before, and it held no header matrices

            # Memory-map data_2 for selective row access.
            # MAT4 is column-major: Fortran order gives correct (mrows, ncols) indexing.
            # For Dymola, mrows = n_trajectory_vars, ncols = n_timesteps (but transposed
            # from MATLAB convention). Dymola stores data_2 as (n_timesteps, n_vars) in
            # MATLAB convention, so mrows=n_timesteps, ncols=n_vars.
            d2_offset, d2_dtype, d2_mrows, d2_ncols = self._block
            if d2_mrows == 0 or d2_ncols == 0:
                # No time point yet: there is nothing to map.
                data_2 = np.empty((max(d2_mrows, 1), 0), dtype=d2_dtype)
            else:
                data_2 = np.memmap(
                    self.path,
                    dtype=d2_dtype,
                    mode="r",
                    offset=d2_offset,
                    shape=(d2_mrows, d2_ncols),
                    order="F",
                )

            if self._time is None:
                self._time = np.array(data_2[0, :])  # Copy time out of memmap
            time = self._time

            if variable_names is None:
                picked = list(self._sources)
            else:
                # The few asked for, not a walk over every name the file has.
                picked = sorted(
                    self._order.keys() & set(variable_names),
                    key=self._order.__getitem__,
                )

            result = {}
            for name in picked:
                row, negate, constant = self._sources[name]
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
            self._stamp = None  # nothing of a failed read is kept
            logger.error("Failed to read %s: %s", self.path, e)
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

    Returns dict with variable names as keys and (time, values) tuples —
    over the whole time points the file HOLDS, which for a run in progress
    or a killed one is fewer than the run asked for (and none at all before
    its first point: every series is then empty). Whether the file is a
    finished result is :func:`mat_shortfall`'s question, not this one's.

    One read of one file: a caller that reads the same file again and again
    keeps a :class:`ResultMatFile`, which parses its header once.
    """
    return ResultMatFile(mat_path).series(variable_names)


def _time_at(f, block, point: int) -> float:
    """The time of one time point: row 0 of the trajectory's column."""
    offset, dtype, mrows, _ = block
    f.seek(offset + point * mrows * dtype.itemsize)
    return float(np.frombuffer(f.read(dtype.itemsize), dtype=dtype)[0])


def read_mat_time_extents(mat_path: Path) -> tuple[float, float] | None:
    """Cheaply read just the first and last time values of a FINISHED
    Dymola .mat.

    Useful for verifying a simulation reached its requested stop time.
    Returns None if the file is unreadable, contains no time data, or was
    not finished by its writer (``_Scan.finished``): the rows of a killed
    run are readable to where they stop, and a run whose last row reached
    the stop time under a header that was never completed still did not
    finish. This is the verdict's reading; :func:`read_result_mat` is the
    one that shows what is there.

    Bypasses the full variable-iteration in read_result_mat — Dymola stores
    time as row 0 of data_2, so we only need two values from disk.
    """
    try:
        with open(mat_path, "rb") as f:
            scan = _scan_mat4_headers(f)
            # review 2026-07-06 (finding 74) applies here too: in a binNormal
            # file data_2 is stored untransposed, so row 0 is not time — the
            # extents would be garbage. The raise lands in the handler below.
            _raise_if_binnormal(f, scan.blocks, mat_path)
            if not scan.finished:
                return None
            block = scan.blocks[_TRAJECTORY]
            return _time_at(f, block, 0), _time_at(f, block, block[3] - 1)
    except Exception:
        return None


def read_mat_last_time(mat_path: Path) -> float | None:
    """The time of the last whole time point ``mat_path`` HOLDS, finished
    or not: how far a killed run got. ``None`` for a file that is
    unreadable or holds no point. Never a verdict —
    :func:`read_mat_time_extents` is, and answers only a finished file."""
    try:
        with open(mat_path, "rb") as f:
            scan = _scan_mat4_headers(f)
            _raise_if_binnormal(f, scan.blocks, mat_path)
            block = scan.blocks.get(_TRAJECTORY)
            if block is None or block[3] == 0:
                return None
            return _time_at(f, block, block[3] - 1)
    except Exception:
        return None


def mat_reaches_time(
    mat_path: Path,
    target_time: float,
) -> tuple[bool, float | None]:
    """Did the trajectory in ``mat_path`` reach ``target_time``?

    Returns ``(reached, last_time)``; ``last_time`` is None when the file
    is unreadable or unfinished (:func:`read_mat_time_extents`). The
    relative tolerance mirrors the persistent-OM disk-rescue rule so
    success-path and rescue-path verdicts can't disagree about the same
    file (D99).
    """
    extents = read_mat_time_extents(mat_path)
    if extents is None:
        return False, None
    last_time = extents[1]
    tol = max(1e-6, abs(target_time) * 1e-6)
    return last_time + tol >= target_time, last_time


def mat_shortfall(mat_path: Path) -> str | None:
    """Why ``mat_path`` is not a finished result, in words for a person —
    ``None`` when it is one.

    The scored read asks this beside :func:`read_result_mat`: the rows of
    an unfinished file are returned to be SHOWN, and this is what keeps
    them from being scored or accepted as a baseline.
    """
    name = Path(mat_path).name
    try:
        with open(mat_path, "rb") as f:
            scan = _scan_mat4_headers(f)
            _raise_if_binnormal(f, scan.blocks, mat_path)
            if scan.finished:
                return None
            block = scan.blocks.get(_TRAJECTORY)
            if block is None:
                return f"{name} unreadable"
            points = block[3]
            if points == 0:
                return f"Empty result: {name} holds no time point"
            held = f"{points} time point{'s' if points != 1 else ''}"
            held += f" up to t={_time_at(f, block, points - 1):.6g}"
            if scan.trailing:
                held += f" and {scan.trailing} bytes of a further one"
            return (
                f"Partial result: {name} holds {held}, its header counts "
                f"{scan.declared} — the run did not finish writing it"
            )
    except Exception:
        return f"{name} unreadable"


def _as_bytes(codes: np.ndarray) -> np.ndarray | None:
    """A 2-D matrix of character codes as one byte a code, where every code
    — truncated, as ``int()`` truncates a float one — is a byte's (0..255):
    Latin-1 decodes those exactly as ``chr`` does. None for any other
    matrix, an empty one included; it is then read a character at a time."""
    if codes.size == 0 or codes.shape[1] == 0:
        return None
    if codes.dtype.kind == "f":
        if not np.isfinite(codes).all():
            return None
        codes = np.trunc(codes)
    if codes.min() < 0 or codes.max() > 255:
        return None
    return np.ascontiguousarray(codes, dtype=np.uint8)


def _parse_name_matrix(name_matrix: np.ndarray) -> list[str]:
    """Parse Dymola's name matrix into a list of variable name strings.

    Dymola stores names as a 2D integer array (n_vars x max_name_len)
    where each row contains character codes for one variable name.
    """
    if name_matrix.ndim == 2 and name_matrix.dtype.kind in ("u", "i", "f"):
        # 2D integer/float array — each row is char codes for one name
        packed = _as_bytes(name_matrix)
        if packed is not None:
            # A row at a time: a character at a time was 0.3 s of a
            # 20,000-variable file's header, for every read of it.
            raw, width = packed.tobytes(), packed.shape[1]
            return [
                raw[start : start + width].decode("latin1").strip("\x00").strip()
                for start in range(0, len(raw), width)
            ]
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
