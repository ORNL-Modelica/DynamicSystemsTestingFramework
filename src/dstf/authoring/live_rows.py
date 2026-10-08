"""A running test's page as data (D151): what the live reader's snapshots
become on the way to a plot, and the words every state of a run that has no
result yet — or will never have one — is said in. Pure: no Session, no UI,
no file. `authoring.live_runs` polls the files and holds the traces; the
Test page, the strip and the queue panel draw from what is here.

:class:`LiveTrace` is what a poller keeps of one run: each asked-for name's
samples so far, dropped when the reader starts over on another file
(``LiveSnapshot.run``), and handed to a plot under the figure layer's sample
cap. The decimation is the figure layer's own (LTTB) and costs the same
whatever it is given, so it is not run on every poll: a series past the cap
is decimated once to three quarters of it and its newer samples are drawn
after that as they are, until they fill the last quarter.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from ..backends.common.live_mat import GONE, RUNNING, LiveSnapshot, LiveState
from ..backends.types import VariableText
from ..comparison.tree_spec import SpecNode, spec_to_view
from ..figures.decimate import lttb

#: A criterion of a test whose run is in progress: its row's readout, the
#: tree card's line. No verdict stands beside it.
WAITING = "waiting for result"
#: A criterion of a test whose last run did not finish.
NOT_SCORED = "not scored"
#: Under the strip of a running test on a backend whose result file does
#: not grow while it runs (no ``Capability.LIVE_RESULTS``).
NOT_AVAILABLE = "live view not available for this backend"
#: A run that has written no row yet: no file, or its header only.
STARTING_WORD = "starting"

#: How an unfinished run ended, as its strip starts.
CANCELLED = "Cancelled"
TIMED_OUT = "Timed out"
FAILED = "Failed"

#: The name of the shape :func:`stopped_marker` adds.
STOPPED = "stopped"

#: Samples a :class:`LiveTrace` holds per name before it thins them: an
#: hour of a run that writes faster than the reader serves is 7 million
#: rows a name.
BOUND = 200_000


def seconds(value: float) -> str:
    """A time in seconds as a person reads it: three figures, the whole
    number from 100 up, and no exponent — ``42``, ``19.2``, ``5.85``,
    ``1235``."""
    if abs(value) >= 100:
        return f"{value:.0f}"
    return np.format_float_positional(
        value, precision=3, unique=False, fractional=False, trim="-"
    )


def progress(state: LiveState, time: float | None, stop_time: float | None) -> str:
    """How far a running test has got, from its file: ``t = 42 / 100 s``,
    ``t = 42 s`` where the stop time is not known, ``starting`` until the
    file holds a row."""
    if state != RUNNING or time is None:
        return STARTING_WORD
    if stop_time is None:
        return f"t = {seconds(time)} s"
    return f"t = {seconds(time)} / {seconds(stop_time)} s"


def how_ended(*, cancelled: bool, timed_out: bool) -> str:
    """The word for a run that ended without finishing. A stop that was
    asked for is a cancel whatever the backend then reported (the pool's
    rule)."""
    if cancelled:
        return CANCELLED
    return TIMED_OUT if timed_out else FAILED


@dataclass(frozen=True)
class Partial:
    """What an unfinished run left: how it ended, the time its result file
    reached, and the stop time it was asked for (None when not known)."""

    how: str
    time: float
    stop_time: float | None = None

    @property
    def sentence(self) -> str:
        """The strip's: ``Cancelled at t = 19.2 of 200 s — not scored``."""
        of = "" if self.stop_time is None else f" of {seconds(self.stop_time)}"
        return f"{self.how} at t = {seconds(self.time)}{of} s — {NOT_SCORED}"

    @property
    def marker(self) -> str:
        """The plot's: ``stopped at t = 19.2 s``."""
        return f"{STOPPED} at t = {seconds(self.time)} s"


# -- snapshots to series -----------------------------------------------------------


class _Samples:
    """One name's samples of one run, bounded: past ``bound`` they are
    thinned to every second one and the stride doubles, for the samples
    still to come too — so what is held stays evenly spaced over the run.
    The newest sample is held beside them when the stride skipped it: a
    trace always ends where the run has got to."""

    def __init__(self, bound: int) -> None:
        self.bound = bound
        self.time = np.empty(0)
        self.values = np.empty(0)
        self.taken = 0  # samples handed in, before any thinning
        self._stride = 1
        self._skip = 0  # incoming samples to pass over before the next kept
        self._newest: tuple[float, float] | None = None
        #: The decimated head (`drawn`) and how many HELD samples it covers:
        #: the newest one beside them is never in it.
        self._head: tuple[np.ndarray, np.ndarray] | None = None
        self._head_of = 0

    def add(self, time: np.ndarray, values: np.ndarray) -> None:
        n = len(time)
        if n == 0:
            return
        self.taken += n
        kept = range(self._skip, n, self._stride)
        if kept:
            self.time = np.concatenate([self.time, time[kept.start :: kept.step]])
            self.values = np.concatenate([self.values, values[kept.start :: kept.step]])
            self._skip = kept[-1] + self._stride - n
        else:
            self._skip -= n
        last_kept = bool(kept) and kept[-1] == n - 1
        self._newest = None if last_kept else (float(time[-1]), float(values[-1]))
        if len(self.time) > self.bound:
            self._thin()

    def _thin(self) -> None:
        odd = len(self.time) % 2 == 1  # whether the last held sample stays
        if not odd and self._newest is None:
            self._newest = (float(self.time[-1]), float(self.values[-1]))
        self.time, self.values = self.time[::2], self.values[::2]
        if odd:
            # The next sample of the old stride is now one to pass over.
            self._skip += self._stride
        self._stride *= 2
        self._head = None

    def arrays(self) -> tuple[np.ndarray, np.ndarray]:
        """Everything held, the newest sample last."""
        if self._newest is None:
            return self.time, self.values
        t, v = self._newest
        return np.append(self.time, t), np.append(self.values, v)

    def drawn(self, cap: int) -> tuple[np.ndarray, np.ndarray]:
        time, values = self.arrays()
        if len(time) <= cap:
            return time, values
        head = cap - cap // 4
        if self._head is None or len(time) - self._head_of > cap - head:
            self._head = lttb(self.time, self.values, head)
            self._head_of = len(self.time)
        return (
            np.concatenate([self._head[0], time[self._head_of :]]),
            np.concatenate([self._head[1], values[self._head_of :]]),
        )


class LiveTrace:
    """One run's file as its poller has seen it: the reader's state, the
    time it reached, and each asked-for name's samples so far — every
    snapshot goes through :meth:`take`. ``cap`` is the most samples a
    series is DRAWN with (the figure layer's budget)."""

    def __init__(self, cap: int, *, bound: int = BOUND) -> None:
        self.cap = max(4, int(cap))
        self.bound = bound
        self.run: int | None = None
        self.state: LiveState = GONE
        self.time: float | None = None
        self._names: dict[str, _Samples] = {}
        self._moved: set[str] = set()

    def take(self, snapshot: LiveSnapshot) -> None:
        """One poll's findings. A snapshot of another run than the last
        (the reader started over: a rerun wiped the file) drops every
        sample first."""
        if snapshot.run != self.run:
            self.run = snapshot.run
            self._names.clear()
        self.state, self.time = snapshot.state, snapshot.time
        self._moved = set()
        for name, series in snapshot.series.items():
            samples = self._names.get(name)
            if samples is None:
                samples = self._names[name] = _Samples(self.bound)
            if len(series.time):
                samples.add(series.time, series.values)
                self._moved.add(name)

    def moved(self, name: str) -> bool:
        """Whether the last snapshot brought ``name`` a sample."""
        return name in self._moved

    def series(self, name: str) -> tuple[np.ndarray, np.ndarray] | None:
        """``name``'s samples so far as a plot draws them — at most ``cap``,
        the last the newest — or None for a name no snapshot has served."""
        samples = self._names.get(name)
        return None if samples is None else samples.drawn(self.cap)

    def samples(self, name: str) -> int:
        """How many samples of ``name`` the snapshots held, drawn or not."""
        samples = self._names.get(name)
        return 0 if samples is None else samples.taken

    def held(self, name: str) -> int:
        samples = self._names.get(name)
        return 0 if samples is None else len(samples.arrays()[0])


# -- the criteria, unscored --------------------------------------------------------


def unscored_view(spec: SpecNode, texts: Mapping[str, VariableText], word: str) -> dict:
    """The criteria tree of a test that has nothing scored and is not
    without a reason — its run is in progress (``word``: :data:`WAITING`)
    or did not finish (:data:`NOT_SCORED`) — in the shape the report's
    `build_tree_view` gives a scored one, so the page's rows draw from it:
    every node carries ``unscored`` (the word) and no ``passed``, and a leaf
    says the word where a verdict's readout goes (``criterion``)."""
    view = spec_to_view(spec)

    def stamp(node: dict, under_warn: bool) -> None:
        node["unscored"] = word
        if node.get("kind") == "leaf":
            name = str(node.get("variable"))
            entry = texts.get(name)
            node.update(
                name=name,
                label=entry.text if entry is not None else name,
                criterion=word,
                advisory=under_warn,
            )
            return
        warn = under_warn or node.get("combinator") == "warn"
        for child in node.get("children") or []:
            stamp(child, warn)

    stamp(view, False)
    return view


# -- the figure ----------------------------------------------------------------------


def spanned(figure: dict, stop_time: float | None) -> dict:
    """``figure`` with its time axis spanning the whole run, 0 to
    ``stop_time``, so a growing trace grows INTO the plot; as it was with
    no stop time. The range is the same in every figure of the run, which
    is what lets a zoom survive each of them (the figure's ``uirevision``).
    A copy: nothing is written into the figure handed in."""
    if stop_time is None or not stop_time > 0:
        return figure
    layout = figure["layout"]
    axis = {**layout.get("xaxis", {}), "range": [0.0, float(stop_time)]}
    return {**figure, "layout": {**layout, "xaxis": axis}}


def stopped_marker(figure: dict, partial: Partial, colour: str) -> dict:
    """``figure`` with a vertical line where an unfinished run stopped and
    its words beside it, in ``colour`` — the theme's muted text. A copy."""
    layout = figure["layout"]
    line = {
        "type": "line",
        "name": STOPPED,
        "xref": "x",
        "yref": "paper",
        "x0": partial.time,
        "x1": partial.time,
        "y0": 0,
        "y1": 1,
        "line": {"color": colour, "width": 1, "dash": "dash"},
        "editable": False,
    }
    note = {
        "name": STOPPED,
        "xref": "x",
        "yref": "paper",
        "x": partial.time,
        "y": 1,
        "xanchor": "right",
        "yanchor": "bottom",
        "text": partial.marker,
        "showarrow": False,
        "font": {"color": colour, "size": 11},
    }
    return {
        **figure,
        "layout": {
            **layout,
            "shapes": [*layout.get("shapes", []), line],
            "annotations": [*layout.get("annotations", []), note],
        },
    }
