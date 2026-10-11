"""A DSresult ``.mat`` read WHILE its run writes it.

:class:`LiveMatReader` is built for one result-file path and polled about
once a second; every :meth:`~LiveMatReader.poll` returns a
:class:`LiveSnapshot` — how far the file has got and, for the names asked
for, the samples that are NEW since those names were last served. It reads
the file the way :mod:`.mat_reader` does (the row count from the file's
size, a variable resolved by the same code: an alias's sign, a parameter as
a constant), so what it returns, concatenated, is what the finished file
reads as.

What it is built around, each measured on Dymola 2026x / omc 1.27 under WSL:

* **Nothing is held between polls.** A poll opens the file, reads, and
  closes it. A persistent worker wipes a test's directory before each run;
  on Windows a handle held open makes that removal fail.
* **No memory map.** A map over a file that is then truncated in place
  kills the process (SIGBUS).
* **Only the new rows are read.** Reading a whole 300 MB result costs over a
  second on a Windows mount; its last rows cost milliseconds. In the steady
  state a poll is one read: the last row already served (to see that the
  file is still the same run) and the rows after it.
* **It never raises** on a file that is short, vanishing or being recreated.
  A poll that finds no file says ``"gone"``; one that finds a file whose
  header matrices are not whole yet says ``"starting"``.

A NEW RUN is a different file under the same path. It is recognised by any
of: the file was gone at a poll, its identity (device, inode) changed, it
got smaller, or the last row served no longer reads back as it did — the
last because a file removed and recreated takes the same inode on ext4, and
can have outgrown the old one before the next poll. The reader then starts
over, and :attr:`LiveSnapshot.run` counts up so its caller drops what it had
accumulated.

A poll returns at most ``max_rows`` samples per name — fewer for a file
with very wide rows, so that no read of rows exceeds 16 MB (one row, where a
single row is larger). When more are new — the
first poll of a run already long under way, a name first asked for late —
they are thinned to that many, evenly spaced and always ending on the newest
row. Thinned rows are not served later: the live trace is a preview, and the
finished file is read by :func:`.mat_reader.read_result_mat`.

A reader belongs to one poller; it is not safe to poll from two threads.
"""

from __future__ import annotations

import os
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal, NamedTuple

import numpy as np

from .mat_reader import (
    _TRAJECTORY,
    _read_tables,
    _scan_mat4_headers,
    _variable_sources,
)

LiveState = Literal["starting", "running", "gone"]

#: A file is there and its header matrices are not whole yet: nothing to read.
STARTING: LiveState = "starting"
#: The header matrices are read; rows are served as they arrive. Also the
#: state of a file whose run has ended — the reader does not judge that.
RUNNING: LiveState = "running"
#: No file at the path: not created yet, or removed.
GONE: LiveState = "gone"

#: Samples one poll returns per name at most (see the module docstring).
MAX_ROWS = 2000

#: No read of rows exceeds this many bytes (one row, where a single row is
#: larger). The rows a poll returns are capped to fit; a thinned backlog
#: spanning less is read in one piece and thinned in memory, a longer one a
#: row at a time.
_READ_BYTES = 16 << 20


@dataclass(frozen=True)
class LiveSeries:
    """One variable's new samples, in the file's own precision. Read-only."""

    time: np.ndarray
    values: np.ndarray


@dataclass(frozen=True)
class LiveSnapshot:
    """What one poll found. Immutable."""

    #: ``"starting"`` / ``"running"`` / ``"gone"``.
    state: LiveState
    #: Which file under the path this is, counted from 0: it goes up each
    #: time the reader starts over on a new run. A caller that accumulates
    #: series drops them when this differs from the snapshot before.
    run: int = 0
    #: Whole rows (time points) the file holds.
    rows: int = 0
    #: The time of the last whole row; ``None`` before the first.
    time: float | None = None
    #: The new samples of each requested name the file has (possibly none:
    #: empty arrays). A name the file lacks is left out, as is every name
    #: until the state is ``"running"``.
    series: Mapping[str, LiveSeries] = field(
        default_factory=lambda: MappingProxyType({})
    )


class _Layout(NamedTuple):
    """The header matrices of one run's file, parsed once."""

    offset: int  # where the trajectory's rows start
    dtype: np.dtype
    n_values: int  # values in a row; the first is the time
    #: name → (index in a row, negate, constant) — :func:`_variable_sources`
    sources: dict[str, tuple[int | None, bool, float | None]]
    #: Every variable the file names, in its order, the abscissa left out:
    #: what `result_files.mat_names` lists of the same file.
    names: tuple[str, ...]

    @property
    def row_bytes(self) -> int:
        return self.n_values * self.dtype.itemsize

    def cap(self, max_rows: int) -> int:
        """Rows one poll returns per name: ``max_rows``, or as many as
        ``_READ_BYTES`` holds when that is fewer."""
        return max(1, min(max_rows, _READ_BYTES // self.row_bytes))


def _read(f, offset: int, n: int) -> bytes:
    f.seek(offset)
    return f.read(n)


class LiveMatReader:
    """Polls one result file for the rows its run has written so far."""

    def __init__(self, path: Path, *, max_rows: int = MAX_ROWS) -> None:
        if max_rows < 1:
            raise ValueError(f"max_rows must be at least 1, got {max_rows}")
        self.path = Path(path)
        self.max_rows = max_rows
        self._run = 0
        self._forget()

    def _forget(self) -> None:
        self._identity: tuple[int, int] | None = None
        self._size = 0
        self._layout: _Layout | None = None
        self._rows = 0
        self._time: float | None = None
        self._last_row = b""
        self._served: dict[str, int] = {}

    def _start_over(self) -> None:
        self._run += 1
        self._forget()

    @property
    def names(self) -> tuple[str, ...] | None:
        """Every variable the file names, from the header the last poll
        read rows through — no second parse of it; None until a poll found
        the header whole."""
        return None if self._layout is None else self._layout.names

    def poll(self, names: Collection[str] = ()) -> LiveSnapshot:
        """The file's state now, and each of ``names``' samples that are new
        since that name was last served (all of them the first time, within
        ``max_rows``). Opens, reads and closes the file; never raises for
        what the file is or is not."""
        try:
            with open(self.path, "rb") as f:
                return self._poll(f, list(dict.fromkeys(names)))
        except (FileNotFoundError, NotADirectoryError):
            if self._identity is not None:
                self._start_over()  # whatever comes next is another run
            return LiveSnapshot(GONE, self._run)
        except OSError:
            # Unreadable right now (a sharing violation, a mount hiccup):
            # no news, and no reason to think the run changed.
            return self._snapshot({})

    def _snapshot(self, series: dict[str, LiveSeries]) -> LiveSnapshot:
        return LiveSnapshot(
            state=STARTING if self._layout is None else RUNNING,
            run=self._run,
            rows=self._rows,
            time=self._time,
            series=MappingProxyType(series),
        )

    def _poll(self, f, names: list[str]) -> LiveSnapshot:
        st = os.fstat(f.fileno())
        identity = (st.st_dev, st.st_ino)
        if self._identity is not None and (
            identity != self._identity or st.st_size < self._size
        ):
            self._start_over()
        self._identity, self._size = identity, st.st_size

        if self._layout is None:
            self._layout = self._read_layout(f)
            if self._layout is None:
                return self._snapshot({})
        layout = self._layout
        present = max(0, st.st_size - layout.offset) // layout.row_bytes
        cap = layout.cap(self.max_rows)

        # Each name's unserved rows, the names grouped by where theirs
        # start: one group in the steady state, a second when a name is
        # asked for the first time.
        groups: dict[int, list[str]] = {}
        for name in names:
            if name in layout.sources:
                groups.setdefault(self._served.get(name, 0), []).append(name)

        series: dict[str, LiveSeries] = {}
        last_row: bytes | None = None
        checked = self._rows == 0  # nothing served yet: nothing to check
        for first, group in sorted(groups.items()):
            # The steady state's one read: from the last row served, which
            # must still read back as it did.
            overlap = not checked and first == self._rows and present - first < cap
            start = first - 1 if overlap else first
            table, got = self._rows_between(f, layout, start, present, cap)
            if overlap:
                if len(table) == 0 or table[0].tobytes() != self._last_row:
                    return self._another_run(f, names)
                checked = True
                table = table[1:]
            if got == present and len(table):
                last_row = table[-1].tobytes()
            for name in group:
                series[name] = self._series(layout, name, table)
                self._served[name] = max(got, first)

        if (
            not checked
            and _read(
                f, layout.offset + (self._rows - 1) * layout.row_bytes, layout.row_bytes
            )
            != self._last_row
        ):
            return self._another_run(f, names)
        if present > self._rows:
            if last_row is None:
                last_row = _read(
                    f,
                    layout.offset + (present - 1) * layout.row_bytes,
                    layout.row_bytes,
                )
            if len(last_row) == layout.row_bytes:
                self._rows, self._last_row = present, last_row
                self._time = float(np.frombuffer(last_row, layout.dtype, 1)[0])
        return self._snapshot(series)

    def _another_run(self, f, names: list[str]) -> LiveSnapshot:
        """The file is not the one the last poll read: start over on it."""
        self._start_over()
        return self._poll(f, names)

    def _read_layout(self, f) -> _Layout | None:
        """The header matrices, or ``None`` while the file does not hold
        them whole (or holds something this reader cannot follow)."""
        try:
            f.seek(0)  # a poll that started over has read rows already
            blocks = _scan_mat4_headers(f).blocks
            if not {"name", "dataInfo", _TRAJECTORY} <= blocks.keys():
                return None
            names, data_info, data_1 = _read_tables(f, blocks, self.path)
            offset, dtype, n_values, _ = blocks[_TRAJECTORY]
            if n_values == 0:
                return None
            sources = {
                name: (index, negate, constant)
                for name, index, negate, constant in _variable_sources(
                    names, data_info, data_1, n_values, self.path
                )
            }
        except Exception:
            # Cut between the scan and the read, or not a binTrans result:
            # whatever a foreign file's bytes decode to, it is not a layout.
            return None
        listed = tuple(n for n in names if n.lower() != "time")
        return _Layout(offset, dtype, n_values, sources, listed)

    @staticmethod
    def _rows_between(
        f, layout: _Layout, first: int, present: int, cap: int
    ) -> tuple[np.ndarray, int]:
        """Rows ``[first, present)`` as a ``(rows, values)`` table — every
        one, or ``cap`` of them evenly spaced and ending on the last — and
        the row count the table reaches (short of ``present`` only when the
        file was cut under the read)."""
        row_bytes = layout.row_bytes
        count = max(0, present - first)
        if count <= cap:
            picks = range(first, first + count)
        else:
            step = -(-count // cap)
            picks = range(present - 1 - step * ((count - 1) // step), present, step)
        if not picks:
            return np.empty((0, layout.n_values), dtype=layout.dtype), first
        span = (picks[-1] - picks[0] + 1) * row_bytes
        if picks.step == 1 or span <= _READ_BYTES:
            raw = _read(f, layout.offset + picks[0] * row_bytes, span)
            whole = len(raw) // row_bytes  # a short read: its whole rows
            table = np.frombuffer(
                raw, dtype=layout.dtype, count=whole * layout.n_values
            ).reshape(whole, layout.n_values)[:: picks.step]
        else:
            rows = []
            for pick in picks:
                raw = _read(f, layout.offset + pick * row_bytes, row_bytes)
                if len(raw) < row_bytes:
                    break
                rows.append(raw)
            table = np.frombuffer(b"".join(rows), dtype=layout.dtype).reshape(
                len(rows), layout.n_values
            )
        reached = picks[len(table) - 1] + 1 if len(table) else first
        return table, reached

    @staticmethod
    def _series(layout: _Layout, name: str, table: np.ndarray) -> LiveSeries:
        index, negate, constant = layout.sources[name]
        time = table[:, 0].copy()
        if index is None:
            # A parameter: its one value at every time point.
            values = np.full_like(time, constant)
        else:
            values = table[:, index].copy()
        if negate:
            values = -values
        time.flags.writeable = False
        values.flags.writeable = False
        return LiveSeries(time, values)
