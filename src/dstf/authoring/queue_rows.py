"""The simulation queue as data (D150): one :class:`QueueRow` per item the
Session's queue lists, in run order, and the header's indicator — the words
each state is said in, the status its badge wears, what its row offers. The
queue panel, the header and the Test page's strip draw from these; no UI
framework here (the Session tier), and no status word of its own
(``reporting.status``)."""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..backends.pool import ItemState
from ..reporting.status import CANCELLED, NOT_RUN, QUEUED, RUNNING, SIM_FAIL, classify
from .capabilities import plural

if TYPE_CHECKING:
    from ..backends.pool import QueueItem, StartingWorker
    from .session import Session

#: What a row may offer, by the name the panel draws it under.
REMOVE, CANCEL, OPEN = "remove", "cancel", "open"

#: A running row a stop was asked of, until it ends.
CANCELLING = "cancelling…"

#: How much of the stopped pool's message the header shows; the rest is its
#: tooltip.
INDICATOR_WIDTH = 48


@dataclass(frozen=True)
class QueueRow:
    """One item of the queue, as its row says it."""

    #: Its place in the queue's list, from 1: the run order (first in,
    #: first out).
    number: int
    model_id: str
    state: ItemState
    #: The state in words: ``waiting · 3 ahead`` (``waiting · next`` with
    #: none, ``waiting · worker starting`` while no worker is up),
    #: ``running · 42 s`` (``running · t = 42 / 100 s`` where the backend
    #: has a live view, D151; ``running · worker restarting`` while its
    #: worker is brought back after a death, ``running · reading the
    #: library`` while its worker reads files that changed since it read
    #: them),
    #: ``cancelling…`` once a stop was asked of a running one and until it
    #: ends, ``finished · 9 s``, ``failed: <the backend's message, one
    #: line>``, ``cancelled``.
    words: str
    #: The badge: QUEUED / RUNNING while the queue holds it, the test's
    #: verdict once it finished, SIM_FAIL for a run that failed, CANCELLED,
    #: and NOT RUN for an item no worker could start.
    status: str
    #: The worker it runs on, from 1, while it runs.
    worker: int | None = None
    #: A running item a stop was asked of (`QueueItem.stopping`): it
    #: offers no second Cancel, and says so until it ends.
    cancelling: bool = False
    #: The items ahead of a waiting one: running, or waiting before it.
    ahead: int = 0
    #: Seconds since it started while it runs; its run's length once ended.
    elapsed: float | None = None
    #: How far a running item's result file has got (``t = 42 / 100 s``,
    #: ``starting`` before its first row), or ``worker restarting``, or
    #: ``reading the library``; None on a backend with no live view, and
    #: for an item that is not running.
    progress: str | None = None
    #: The backend's message for a failed item, in full.
    message: str | None = None
    #: Whether a worker ever took it: False for a waiting item and for one
    #: the pool failed because no worker could start.
    started: bool = False
    #: ``remove`` while it waits, ``cancel`` while it runs on a backend that
    #: can stop a test, ``open`` (its Test page) once it has ended.
    actions: tuple[str, ...] = ()
    #: Why a running item has no ``cancel``: the backend's sentence.
    cancel_why: str | None = None


#: Where Python's traceback starts in a message.
_TRACEBACK = "Traceback (most recent call last):"


def one_line(text: str) -> str:
    """``text`` as one line: its first line that says something — and, for
    a message that carries a Python traceback, what was raised (the
    traceback's LAST line) after whatever the message said before it. The
    first line alone read "Python execution failed: Traceback (most recent
    call last):" for every failing `simulate`."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    head, found, _ = lines[0].partition(_TRACEBACK)
    if found and len(lines) > 1:
        return f"{head.strip()} {lines[-1]}".strip()
    return lines[0]


#: A quoted absolute path in a backend's phase: all but its last part is
#: cut (``loadFile("…/package.mo")``).
_LONG_PATH = re.compile(r'"(?:[A-Za-z]:)?[/\\][^"]*[/\\]([^"/\\]+)"')


#: A running row whose worker died and is being brought back.
RESTARTING = "worker restarting"

#: A running row whose worker is up and reads the library again before the
#: test: its files changed since the worker read them, and a large library
#: is as long to read again as it was to load. The ONE phrase for it — the
#: row's words, the strip's sentence and the worker's line all say this.
READING = "reading the library"


def starting_text(worker: StartingWorker) -> str:
    """One worker that is not ready, as the queue panel lists it:
    ``starting worker 1 — loadFile("…/package.mo") · 85 s``, the phase in
    the backend's own words with a path cut to its last part; ``starting
    worker 1 · 85 s`` where the backend names no phase; ``restarting worker
    1 — …`` for one brought back after a death; ``worker 1 reading the
    library — …`` for one that is up and reads changed files again."""
    phase = _LONG_PATH.sub(r'"…/\1"', worker.phase)
    doing = f" — {phase}" if phase else ""
    number = worker.worker_id + 1
    if worker.reload:
        return f"worker {number} {READING}{doing} · {worker.seconds:.0f} s"
    verb = "restarting" if worker.restart else "starting"
    return f"{verb} worker {number}{doing} · {worker.seconds:.0f} s"


def starting_texts(session: Session) -> list[str]:
    """The queue's workers being brought up right now, by worker."""
    return [starting_text(worker) for worker in session.starting_workers]


def _row(
    session: Session,
    item: QueueItem,
    number: int,
    ahead: int,
    now: float,
    starting: Mapping[int, StartingWorker],
) -> QueueRow:
    state = session.item_state(item)
    started = item.started_at is not None
    fields: dict[str, Any] = {}
    if state is ItemState.WAITING:
        if ahead:
            words = f"waiting · {ahead} ahead"
        else:
            # Nothing ahead and it has not started: no worker is up. One
            # being brought up says so — a library's load is minutes, and
            # "next" for that long reads as a hang.
            words = "waiting · worker starting" if starting else "waiting · next"
        fields = {
            "words": words,
            "status": QUEUED,
            "ahead": ahead,
            "actions": (REMOVE,),
        }
        # Failed by the pool and not yet taken in: no worker took it.
        started = False
    elif state is ItemState.RUNNING:
        elapsed = max(0.0, now - (item.started_at or now))
        why = session.cannot_cancel
        # Its worker died and is being brought back, or is reading the
        # library again: the test has not begun, whatever its file or its
        # clock would say.
        busy = starting.get(item.worker_id) if item.worker_id is not None else None
        live = (
            session.live_runs.progress(item)
            if busy is None
            else (READING if busy.reload else RESTARTING)
        )
        fields = {
            "words": f"running · {live or f'{elapsed:.0f} s'}",
            "progress": live,
            "status": RUNNING,
            "worker": None if item.worker_id is None else item.worker_id + 1,
            "elapsed": elapsed,
            "actions": () if why is not None else (CANCEL,),
            "cancel_why": why,
        }
        if item.stopping:
            # Cancel was taken and the simulation has not ended: one that
            # does not look at the request runs on for its worker's grace
            # (Dymola, a model blocked in an external call: about 11 s),
            # and `running · 42 s` beside a Cancel button said the click
            # went nowhere.
            fields.update(words=CANCELLING, cancelling=True, actions=())
    else:
        ran = (
            item.ended_at - item.started_at
            if item.started_at is not None and item.ended_at is not None
            else None
        )
        fields = {"elapsed": ran, "actions": (OPEN,)}
        if state is ItemState.FINISHED:
            comp = session.comparisons.get(item.model_id)
            fields["status"] = classify(comp) if comp is not None else SIM_FAIL
            fields["words"] = "finished" + (
                f" · {ran:.0f} s" if ran is not None else ""
            )
        elif state is ItemState.CANCELLED:
            fields.update(status=CANCELLED, words="cancelled")
        else:
            message = item.message or "the simulation failed"
            fields.update(
                status=SIM_FAIL if started else NOT_RUN,
                words=f"failed: {one_line(message)}",
                message=message,
            )
    return QueueRow(
        number=number, model_id=item.model_id, state=state, started=started, **fields
    )


def queue_rows(session: Session, *, now: float | None = None) -> list[QueueRow]:
    """The queue's rows, in run order. ``now`` is the clock a running row's
    elapsed seconds are read against (``time.time()``)."""
    clock = time.time() if now is None else now
    starting = {w.worker_id: w for w in session.starting_workers}
    rows: list[QueueRow] = []
    ahead = 0
    for number, item in enumerate(session.queue, start=1):
        row = _row(session, item, number, ahead, clock, starting)
        if row.state in (ItemState.WAITING, ItemState.RUNNING):
            ahead += 1
        rows.append(row)
    return rows


def queue_row(
    session: Session, model_id: str, *, now: float | None = None
) -> QueueRow | None:
    """The row of ``model_id``'s item — the last one listed — or None."""
    return next(
        (r for r in reversed(queue_rows(session, now=now)) if r.model_id == model_id),
        None,
    )


def counts_text(rows: list[QueueRow]) -> str:
    """The panel's header: how many rows are in each state, in the order a
    queue passes through them; ``queue empty`` without a row."""
    parts = [
        f"{n} {word}"
        for word, n in (
            ("running", sum(r.state is ItemState.RUNNING for r in rows)),
            ("queued", sum(r.state is ItemState.WAITING for r in rows)),
            ("finished", sum(r.state is ItemState.FINISHED for r in rows)),
            ("failed", sum(r.state is ItemState.FAILED for r in rows)),
            ("cancelled", sum(r.state is ItemState.CANCELLED for r in rows)),
        )
        if n
    ]
    return " · ".join(parts) if parts else "queue empty"


@dataclass(frozen=True)
class Indicator:
    """The header's queue indicator: ``3 running · 5 queued``, ``idle``, or
    — ``tone`` ``failed`` — the first words of the message the pool stopped
    with, the whole of it in ``tip``."""

    text: str
    tone: str  # "idle" | "running" | "failed"
    tip: str


def indicator(session: Session) -> Indicator:
    rows = queue_rows(session)
    running = sum(r.state is ItemState.RUNNING for r in rows)
    queued = sum(r.state is ItemState.WAITING for r in rows)
    busy = " · ".join(
        f"{n} {word}" for word, n in (("running", running), ("queued", queued)) if n
    )
    stopped = session.queue_stopped
    if stopped is not None:
        line = one_line(stopped)
        short = (
            line
            if len(line) <= INDICATOR_WIDTH
            else line[:INDICATOR_WIDTH].rstrip() + "…"
        )
        return Indicator(
            " · ".join(t for t in (short, busy) if t),
            "failed",
            f"{stopped} — Clear finished in the queue panel dismisses this.",
        )
    if busy:
        return Indicator(busy, "running", "The simulation queue. Click to open it.")
    return Indicator("idle", "idle", "Nothing is queued. Click to open the queue.")


def stop_reason(session: Session) -> str | None:
    """What Stop server says of the queue (brief §3.6: it confirms when the
    queue is not empty): how many tests it still holds, or None."""
    held = sum(
        r.state in (ItemState.WAITING, ItemState.RUNNING) for r in queue_rows(session)
    )
    if not held:
        return None
    return f"{plural(held, 'test')} {'is' if held == 1 else 'are'} queued or running"
