"""The runs whose result the Session does not have (D151): a test the queue
is running, and one whose last run ended without finishing and left a
partial result file. The Session's own tier — it holds the readers and
opens the files; what their snapshots become is `authoring.live_rows`.

A run that STARTS takes its test's result out of memory (D150: the run
cleared it on disk). What the page was laid out by goes with it — the
scored names its plots derive from, the tracked names, the labels — so the
run's entry here keeps the comparison and the result it replaced, for the
LAYOUT alone: no verdict and no sample of them is shown.

While it runs, on a backend whose result file grows
(``Capability.LIVE_RESULTS``), :meth:`LiveRuns.poll` reads the file once a
second — for the names the open pages asked for (:meth:`LiveRuns.ask`), and
with none, the time it has reached, which the queue panel's row says. One
reader per run, polled from the UI loop: two tabs on one test share it.

"In progress" is the queue's word, never the reader's: a finished file
reads ``running`` too. An entry is in progress until the Session says its
item ended (:meth:`LiveRuns.ended`); then it goes — or, where the run did
not finish and its file holds a row, stays as a :class:`~.live_rows.Partial`,
read by the size-based reader's partial read. A partial result is shown:
nothing here hands it to the comparator or to the store.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from ..backends.common.live_mat import RUNNING, LiveMatReader
from ..backends.common.mat_reader import read_mat_last_time
from ..backends.common.result_files import mat_series
from ..backends.pool import ItemState
from ..backends.types import Capability, TestResult, VariableResult
from ..discovery.spec_update import is_literal_variable
from ..discovery.unit_tests import unit_test_variables
from .live_rows import (
    NOT_SCORED,
    STARTING_WORD,
    WAITING,
    LiveTrace,
    Partial,
    how_ended,
    progress,
    run_revision,
    spanned,
    stopped_marker,
)

if TYPE_CHECKING:
    from ..backends.pool import QueueItem
    from ..comparison.types import TestComparison
    from ..discovery.test_registry import TestModel
    from .session import Session

#: Polls a page's ask outlives: a page that stopped asking (closed, moved to
#: another test) costs its names two more reads.
ASK_LIFE = 2

Series = tuple[np.ndarray, np.ndarray]


def expected_result(test: TestModel) -> TestResult | None:
    """What a test that never had a result is laid out by while its first
    run is in progress, and after one that left a partial file: the
    variables its model and its spec NAME — the UnitTests variables with
    their expressions, then every ``variables`` entry that names one (a
    pattern names none until a file is matched against it). No sample of
    any: a layout, never a trace. None for a test that names none."""
    named = {v.name: v.expression for v in unit_test_variables(test)}
    for pattern in test.variable_patterns:
        if is_literal_variable(pattern):
            named.setdefault(pattern, "")
    if not named:
        return None
    empty = np.empty(0)
    return TestResult(
        model_id=test.model_id,
        success=True,
        variables=[
            VariableResult(
                index=index, time=empty, values=empty, name=name, expression=text
            )
            for index, (name, text) in enumerate(named.items(), start=1)
        ],
    )


@dataclass
class _Run:
    """One model's run with no result: in progress, or (``partial``) ended
    without finishing."""

    item: QueueItem
    #: What the run replaced, for the page's layout.
    comparison: TestComparison | None
    result: TestResult | None
    #: Whether the backend's result file grows while the run is in progress.
    live: bool
    trace: LiveTrace
    #: The ``uirevision`` of this run's figures, its partial result's too.
    revision: str
    reader: LiveMatReader | None = None
    #: The names each page asked for, and the poll it last asked at.
    asked: dict[object, tuple[frozenset[str], int]] = field(default_factory=dict)
    #: The growing file's variables, listed once per file (`names`).
    names: tuple[str, ...] | None = None
    #: The reader's ``run`` whose file the pages were told can be read.
    announced: int | None = None
    partial: Partial | None = None
    #: A partial file's series, by name; None is a name it lacks.
    series: dict[str, Series | None] = field(default_factory=dict)

    @property
    def stop_time(self) -> float | None:
        return self.item.test.stop_time


class LiveRuns:
    def __init__(
        self, session: Session, emit: Callable[[str, frozenset[str]], None]
    ) -> None:
        """``emit(state, models)`` says a `queue` event the Session's
        way: a run's file became readable, and its pages re-read what they
        list of it."""
        self._session = session
        self._emit = emit
        self._runs: dict[str, _Run] = {}
        self._polls = 0
        self._starts = 0

    # -- the Session's calls ---------------------------------------------------

    def started(self, item: QueueItem) -> None:
        """``item`` started — called BEFORE the Session drops its test's
        result. A test started again after a run that left none (a cancel)
        keeps the layout that run kept; one that never had a result is laid
        out by what it names (`expected_result`)."""
        session = self._session
        project = session.project
        if project is None:
            return
        model_id = item.model_id
        comparison = session.comparisons.get(model_id)
        result = project.results.get(model_id)
        before = self._runs.get(model_id)
        if result is None or not result.success:
            if before is not None:
                comparison, result = before.comparison, before.result
            else:
                result = None  # a failed run's: it holds no variable
            if result is None:
                result = expected_result(item.test)
        self._starts += 1
        self._runs[model_id] = _Run(
            item=item,
            comparison=comparison,
            result=result,
            live=self._live(),
            trace=self._trace(),
            revision=run_revision(self._starts),
        )

    def ended(self, item: QueueItem) -> None:
        """``item`` ended, as the QUEUE says — the reader's state is not
        asked. A finished run's entry goes: the page shows its result. So
        does an unfinished one's that left no row; one that left a partial
        file stays, to be shown and never scored."""
        model_id = item.model_id
        run = self._runs.get(model_id)
        if run is None or run.item is not item:
            return
        run.reader, run.trace, run.asked = None, self._trace(), {}
        path = self._path(run)
        reached = (
            read_mat_last_time(path)
            if item.state is not ItemState.FINISHED and path is not None
            else None
        )
        if reached is None:
            del self._runs[model_id]
            return
        outcome = item.run_result
        run.partial = Partial(
            how_ended(
                cancelled=item.state is ItemState.CANCELLED,
                timed_out=outcome is not None and outcome.timed_out,
            ),
            reached,
            run.stop_time,
        )

    def clear(self) -> None:
        """The project is closing: nothing of its runs is kept."""
        self._runs.clear()

    # -- what a page asks ------------------------------------------------------

    def ask(self, model_id: str, page: object, names: Collection[str]) -> None:
        """``page`` draws ``names`` of the running ``model_id``: the next
        polls read them. Asked again every second it still does."""
        run = self._runs.get(model_id)
        if run is not None and run.partial is None and run.live:
            run.asked[page] = (frozenset(names), self._polls)

    def poll(self) -> None:
        """Read every run in progress whose file grows, once: the names its
        pages asked for, and how far it has got. On the UI loop."""
        self._polls += 1
        for model_id, run in list(self._runs.items()):
            if run.partial is not None or not run.live:
                continue
            if run.reader is None:
                path = self._path(run)
                if path is None:
                    continue  # its directory is not keyed yet
                run.reader = LiveMatReader(path, max_rows=run.trace.cap)
            run.asked = {
                page: (names, at)
                for page, (names, at) in run.asked.items()
                if self._polls - at <= ASK_LIFE
            }
            names = frozenset().union(*(n for n, _ in run.asked.values()))
            run.trace.take(run.reader.poll(sorted(names)))
            if run.trace.run != run.announced and run.trace.state == RUNNING:
                # This file's header is whole: its variables can be listed,
                # and a file that replaced another's is listed again.
                run.announced, run.names = run.trace.run, None
                self._emit("live", frozenset({model_id}))

    def polled(self, model_id: str) -> bool:
        """Whether ``model_id`` is running with a live view."""
        run = self._runs.get(model_id)
        return run is not None and run.partial is None and run.live

    def moved(self, model_id: str, name: str) -> bool:
        """Whether the last poll brought ``name`` of ``model_id`` a sample."""
        run = self._runs.get(model_id)
        return run is not None and run.partial is None and run.trace.moved(name)

    # -- what the Session reads ------------------------------------------------

    def kept(
        self, model_id: str
    ) -> tuple[TestComparison | None, TestResult | None] | None:
        """The comparison and the result ``model_id``'s run replaced — what
        its page is laid out by while the Session has no result of it — or
        None for a model with no entry here."""
        run = self._runs.get(model_id)
        return None if run is None else (run.comparison, run.result)

    def word(self, model_id: str) -> str | None:
        """What every criterion of ``model_id`` says in place of a verdict:
        waiting while its run is in progress, not scored after one that did
        not finish; None for a model with no entry here."""
        run = self._runs.get(model_id)
        if run is None:
            return None
        return WAITING if run.partial is None else NOT_SCORED

    def partial(self, model_id: str) -> Partial | None:
        run = self._runs.get(model_id)
        return None if run is None else run.partial

    def progress(self, item: QueueItem) -> str | None:
        """``t = 42 / 100 s`` (or ``starting``) for a running ``item`` on a
        backend with a live view; None on one without."""
        if not self._live():
            return None
        run = self._runs.get(item.model_id)
        if run is None or run.item is not item or run.partial is not None:
            # Running by the queue's word, its start still on its way to
            # the Session (a tick): nothing is read yet.
            return STARTING_WORD
        return progress(run.trace.state, run.trace.time, run.stop_time)

    def names(self, model_id: str) -> tuple[str, ...] | None:
        """The variables of a running ``model_id``'s growing file, listed
        once its header is whole; None before, and on a backend without a
        live view."""
        run = self._runs.get(model_id)
        if run is None or run.partial is not None or run.announced is None:
            return None
        if run.names is None:
            test_dir = self._dir(run)
            listed = (
                type(self._backend()).result_names(test_dir)
                if test_dir is not None
                else None
            )
            run.names = tuple(listed or ())
        return run.names

    def series(self, model_id: str, name: str) -> tuple[Series | None, int] | None:
        """What ``model_id``'s page draws as ``name``'s Actual in place of a
        result's: ``(series, samples)`` — the samples read so far while its
        run is in progress (none yet: empty; None on a backend without a
        live view), a partial file's after one that did not finish
        (``samples`` 0: the whole series, counted by whoever decimates it).
        None for a model with no entry here."""
        run = self._runs.get(model_id)
        if run is None:
            return None
        if run.partial is None:
            if not run.live:
                return None, 0
            # Empty before the first sample, never absent: the Actual trace
            # keeps its place in the figure from the run's first second, so
            # the legend's choices stay on the traces they were made on.
            series = run.trace.series(name)
            return series or (np.empty(0), np.empty(0)), run.trace.samples(name)
        if name not in run.series:
            path = self._path(run)
            read = mat_series(path, [name]) if path is not None else {}
            run.series[name] = read.get(name)
        return run.series[name], 0

    def dress(self, model_id: str, figure: dict, colour: str) -> dict:
        """``figure`` as ``model_id``'s page draws it: one of its run's own
        revision, the time axis spanning the whole run while a live view
        grows into it, and after an unfinished run the marker where it
        stopped, in ``colour``."""
        run = self._runs.get(model_id)
        if run is None or not run.live:
            return figure
        figure = spanned(figure, run.stop_time, run.revision)
        if run.partial is None:
            return figure
        return stopped_marker(figure, run.partial, colour)

    # -- internals ---------------------------------------------------------------

    def _live(self) -> bool:
        """Whether the project's backend writes a result file that grows
        while a run is in progress."""
        project = self._session.project
        return (
            project is not None
            and Capability.LIVE_RESULTS in project.backend.capabilities
        )

    def _trace(self) -> LiveTrace:
        project = self._session.project
        budget = project.config.max_embedded_samples if project is not None else 0
        return LiveTrace(cap=budget if budget and budget > 0 else 1000)

    def _backend(self):
        project = self._session.project
        assert project is not None  # an entry exists only under a project
        return project.backend

    def _dir(self, run: _Run) -> Path | None:
        """The run's directory, once it is keyed — a moment after its item
        turns RUNNING, its last run's files cleared by then."""
        project = self._session.project
        key = run.item.test_key
        if project is None or key is None:
            return None
        work_dir = project.config.work_dir
        assert work_dir is not None  # resolved in Config.__post_init__
        return work_dir / key

    def _path(self, run: _Run) -> Path | None:
        """The run's growing result file; None on a backend without one,
        and until its directory is keyed."""
        test_dir = self._dir(run)
        if test_dir is None or not run.live:
            return None
        return type(self._backend()).live_result_path(test_dir)
