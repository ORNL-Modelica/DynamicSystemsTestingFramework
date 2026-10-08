"""Synthetic DSresult v4 files for the reader tests — written the way Dymola
and OpenModelica write theirs: ``Aclass``, ``name``, ``description``,
``dataInfo``, ``data_1``, then ``data_2`` LAST, its header before its rows.

A file can be written with only its first ``points`` rows and with any
column count in ``data_2``'s header (``declared``), which is how a run in
progress looks: Dymola's count lags the rows, OpenModelica's is 0 until the
end. ``ResultMat.rows`` gives the bytes of further rows to append.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_P_CODE = {np.dtype("<f8"): 0, np.dtype("<f4"): 1, np.dtype("<i4"): 2}


@dataclass(frozen=True)
class ResultMat:
    """A written file: where each matrix sits and every row of ``data_2``."""

    path: Path
    #: matrix name → (header start, data start, end) in bytes
    spans: dict[str, tuple[int, int, int]]
    #: ``data_2`` whole, one row per time point: ``(points, 1 + trajectories)``
    table: np.ndarray
    #: every variable's expected values over all points, by name
    expected: dict[str, np.ndarray]

    @property
    def data_offset(self) -> int:
        return self.spans["data_2"][1]

    @property
    def row_bytes(self) -> int:
        return self.table.shape[1] * self.table.dtype.itemsize

    @property
    def time(self) -> np.ndarray:
        return self.table[:, 0]

    def rows(self, first: int, last: int | None = None) -> bytes:
        """The bytes of rows ``[first, last)`` as the file stores them."""
        return self.table[first:last].tobytes(order="C")


def _chars(strings: list[str]) -> np.ndarray:
    """Char codes stored (max_len, n_strings) — binTrans, one string a column."""
    m = np.zeros((max(len(s) for s in strings), len(strings)), dtype=np.uint8)
    for j, s in enumerate(strings):
        m[: len(s), j] = [ord(c) for c in s]
    return m


def _header(name: str, mopt: int, mrows: int, ncols: int) -> bytes:
    raw = name.encode("ascii") + b"\x00"
    return struct.pack("<5i", mopt, mrows, ncols, 0, len(raw)) + raw


def write_result_mat(
    path: Path,
    *,
    tool: str = "dymola",
    dtype: str = "<f4",
    time: np.ndarray,
    trajectories: dict[str, np.ndarray],
    parameters: dict[str, float] | None = None,
    aliases: dict[str, tuple[str, bool]] | None = None,
    points: int | None = None,
    declared: int | None = None,
    aclass: str = "binTrans",
) -> ResultMat:
    """Write a result file and say what is in it.

    ``aliases`` maps a name to ``(the variable it aliases, negated)`` — the
    target a trajectory or a parameter. ``points`` rows of ``data_2`` are
    written (all by default) under a header that counts ``declared`` of them
    (the rows written by default). ``tool`` picks the two layouts' one
    difference a reader could trip on: the abscissa's name and whether the
    parameters are stored as float64 beside a float32 trajectory.
    """
    parameters = parameters or {}
    aliases = aliases or {}
    dt = np.dtype(dtype)
    time = np.asarray(time, dtype=dt)
    table = np.column_stack(
        [time, *(np.asarray(v, dtype=dt) for v in trajectories.values())]
    ).astype(dt)
    data_1_dtype = np.dtype("<f8") if tool == "openmodelica" else dt
    stop = float(time[-1]) if len(time) else 0.0
    data_1 = np.array(
        [[0.0, stop], *([v, v] for v in parameters.values())], dtype=data_1_dtype
    )

    # dataInfo: (matrix, 1-based column — negative for the opposite sign,
    # interpolation, protected)
    names = ["Time" if tool == "dymola" else "time"]
    info = [[0, 1, 0, -1]]
    where: dict[str, tuple[int, int]] = {}
    for col, name in enumerate(trajectories, start=2):
        where[name] = (2, col)
    for col, name in enumerate(parameters, start=2):
        where[name] = (1, col)
    for name, (matrix, col) in where.items():
        names.append(name)
        info.append([matrix, col, 0, -1 if tool == "dymola" else 0])
    expected = {n: table[:, c - 1].copy() for n, (m, c) in where.items() if m == 2}
    expected.update(
        {
            n: np.full_like(time, float(data_1[c - 1, 0]))
            for n, (m, c) in where.items()
            if m == 1
        }
    )
    for name, (target, negated) in aliases.items():
        matrix, col = where[target]
        names.append(name)
        info.append([matrix, -col if negated else col, 0, 0])
        expected[name] = -expected[target] if negated else expected[target].copy()

    written = len(time) if points is None else points
    matrices = [
        ("Aclass", 51, _chars(["Atrajectory", "1.1", "", aclass])),
        ("name", 51, _chars(names)),
        ("description", 51, _chars([f"the {n}" for n in names])),
        ("dataInfo", 20, np.array(info, dtype="<i4").T),
        ("data_1", _P_CODE[data_1_dtype] * 10, data_1),
    ]
    spans: dict[str, tuple[int, int, int]] = {}
    with open(path, "wb") as f:
        for name, mopt, matrix in matrices:
            start = f.tell()
            f.write(_header(name, mopt, *matrix.shape))
            data = f.tell()
            f.write(matrix.tobytes(order="F"))
            spans[name] = (start, data, f.tell())
        start = f.tell()
        f.write(
            _header(
                "data_2",
                _P_CODE[dt] * 10,
                table.shape[1],
                written if declared is None else declared,
            )
        )
        data = f.tell()
        f.write(table[:written].tobytes(order="C"))
        spans["data_2"] = (start, data, f.tell())
    return ResultMat(path=path, spans=spans, table=table, expected=expected)
