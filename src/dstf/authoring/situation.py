"""A test's situation, as the Test page's status strip says it: what state
the test is in, the tone the strip is drawn in, and the one sentence that
says it. Pure — no Session, no UI: the page reads the inputs, this decides.

The situations are checked in :class:`Situation`'s order; the first that
applies wins (a test the queue holds before pending edits, edits before
anything the last scoring said).

There is no state for edits to the simulation settings (the owner's ruling
Q2 A drew one): ``/simulation`` is not stageable — `patch_apply`'s default
whitelist is ``/comparison``, ``/metrics`` and ``/plots``, and widening it
changes ``spec update``, a seam another repository relies on. It waits for
a simulation-settings editor."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from ..backends.pool import ItemState
from ..comparison.tree_spec import tree_variables
from ..reporting.status import FAIL, NO_REF, NOT_RUN, PASS, SIM_FAIL
from .capabilities import plural
from .live_rows import CANCELLED, NOT_AVAILABLE
from .queue_rows import one_line

if TYPE_CHECKING:
    from ..backends.types import TestResult
    from ..discovery.test_registry import TestModel
    from .live_rows import Partial
    from .queue_rows import QueueRow


class Situation(Enum):
    #: the simulation queue holds the test, waiting (D150)
    QUEUED = "queued"
    RUNNING = "running"
    EDITS = "edits"
    #: pending edits whose criteria name a variable the last run did not
    #: record: Queue is the one next action, Save is quiet (the owner's O1)
    EDITS_NEW = "edits_new"
    SCORING_FAILED = "scoring_failed"
    WAITING = "waiting"
    #: its last run in the queue was stopped: it has no result, and the
    #: one before was replaced when that run started
    CANCELLED = "cancelled"
    #: the queue could not start it — no worker could start: the
    #: backend's message; its last result stands
    START_FAILED = "start_failed"
    NO_RESULT = "no_result"
    SIM_FAIL = "sim_fail"
    #: saved criteria name a variable the last run did not record: a
    #: baseline accepted now would not hold it (the owner's O3)
    NEW_VARIABLES = "new_variables"
    NO_REF = "no_ref"
    #: the comparison refused the test (`reporting.status.refusal`): it
    #: fails with a reason and nothing scored, and accepting does not
    #: clear it — no capability names this as its next action
    REFUSED = "refused"
    FAIL = "fail"
    SIMULATE_ONLY = "simulate_only"
    PASS = "pass"


@dataclass(frozen=True)
class Strip:
    situation: Situation
    tone: str  # "grey" | "red" | "amber"
    sentence: str
    #: A line under the strip, said once: why a running test's page shows
    #: no growing trace (D151). Empty for every other strip.
    note: str = ""
    #: What the sentence is one line of — a failed run's message in full,
    #: shown on hover. Empty where the sentence is the whole of it.
    tip: str = ""


_ACCEPT_IT = "If the new behaviour is right, accept it as the baseline."

#: What a failed run's strip ends with: its one next action.
_FIX_IT = "fix the model or its settings, then queue it again."
#: The comparator's words for a failed run that said nothing of why.
_NO_REASON = "Simulation failed"


def _why(failure: str | None) -> str:
    """A failed run's own reason as one line with no full stop — the
    backend's message, as the Suite table's Message column has it — or
    ``""`` where the run recorded none."""
    line = one_line(failure or "").rstrip(".")
    return "" if line == _NO_REASON else line


#: The strip of a test whose run was cancelled (D150).
CANCELLED_SENTENCE = (
    "Cancelled — no result (its earlier result was replaced when the run started)."
)

#: Between the EDITS count and what the edits do to the verdict — in the
#: strip's sentence and on the pending drawer's line under each test.
SEP = " · "


def verdict_phrase(status: str | None, saved_status: str | None) -> str:
    """What the edits do to the verdict: the EDITS clause after the count,
    and the pending drawer's verdict line."""
    if saved_status is None:
        return f"a new entry, scored {status or '…'}"
    if status == saved_status:
        return f"verdict stays {status}"
    return f"verdict would change {saved_status} → {status or '…'}"


def unrecorded_variables(
    test: TestModel | None, result: TestResult | None, diagnostics: Iterable[str]
) -> list[str]:
    """The variables ``test``'s criteria name (`tree_variables`, the rule
    every backend tracks by) that ``result`` does not hold. ``[]`` without
    a result: there is nothing to rerun FOR yet. ``[]`` for a FAILED one,
    which holds no variables: every criterion's would count, and a rerun
    asked for to score them over a simulation that fails."""
    if test is None or result is None or not result.success:
        return []
    have = {v.name for v in result.variables}
    return [
        n for n in tree_variables(test.metric_tree_spec, diagnostics) if n not in have
    ]


def new_variables(
    test: TestModel | None, result: TestResult | None, diagnostics: Iterable[str]
) -> int:
    """How many variables only a rerun records: those of
    :func:`unrecorded_variables`. A variable the result FILE holds is not
    one of them — `Session.stage` re-reads the model when a criterion names
    it (the owner's O2), so the result holds it by the time this counts.
    0 for a simulate-only test: nothing is ever scored there, so no
    variable is one a rerun would score (never NEW_VARIABLES or
    EDITS_NEW)."""
    if test is not None and test.simulate_only:
        return 0
    return len(unrecorded_variables(test, result, diagnostics))


def _queue_it(n: int) -> str:
    return f"queue the test to score {'it' if n == 1 else 'them'}"


def strip_for(
    *,
    queue: QueueRow | None = None,
    edits: int = 0,
    status: str | None = None,
    saved_status: str | None = None,
    has_result: bool = False,
    loading: bool = False,
    scoring_failed: bool = False,
    simulate_only: bool = False,
    fails: int = 0,
    criteria: int = 0,
    advisory: int = 0,
    new_variables: int = 0,
    refused: str | None = None,
    partial: Partial | None = None,
    failure: str | None = None,
) -> Strip:
    """The strip for one test. ``queue`` is its row in the simulation
    queue (`queue_rows.queue_row`), None when the queue lists no item of
    it; ``edits`` its pending ops; ``status`` / ``saved_status`` the
    classifier's word for the comparison as edited / as saved (None while
    unscored, or — saved — for an entry the loaded spec does not have;
    ``reporting.status.SCORING_FAILED`` — saved — when scoring the saved
    spec raised);
    ``has_result`` whether the last run finished it; ``loading`` whether
    the project is still being read — an unscored test is then waiting for
    it, never without a result, which may not be read yet; ``fails`` /
    ``criteria`` / ``advisory`` its leaves failing, in all, and failing
    inside a warn; ``new_variables`` the variables its criteria name that
    the last run did not record (:func:`new_variables`); ``refused`` why
    the comparison refused the test (`reporting.status.refusal`), or None;
    ``partial`` what its last run left when it did not finish
    (`Session.partial`), or None; ``failure`` the message its comparison
    carries (`TestComparison.error_message`) — for a failed run the
    backend's own, read back from the run's stored result, so it is there
    after the queue's row is cleared and after the tool is started again."""
    S = Situation
    if queue is not None and queue.state is ItemState.WAITING:
        return Strip(S.QUEUED, "grey", f"Queued — {queue.words}")
    if queue is not None and queue.state is ItemState.RUNNING:
        where = f" on worker {queue.worker}" if queue.worker is not None else ""
        if queue.cancelling:
            # A stop was asked and it has not ended yet (`QueueRow`).
            return Strip(S.RUNNING, "grey", f"Cancelling{where}…")
        # How far its result file has got, where the backend has a live
        # view (D151); its seconds, and the line that says why, where not.
        if queue.progress is not None:
            return Strip(S.RUNNING, "grey", f"Running{where} — {queue.progress}")
        return Strip(
            S.RUNNING,
            "grey",
            f"Running{where} — {queue.elapsed or 0:.0f} s",
            note=NOT_AVAILABLE,
        )
    if edits:
        sentence = f"{plural(edits, 'unsaved edit')}{SEP}{verdict_phrase(status, saved_status)}"
        if new_variables:
            new = f"{plural(new_variables, 'new variable')}: {_queue_it(new_variables)}"
            return Strip(S.EDITS_NEW, "amber", f"{sentence}{SEP}{new}")
        return Strip(S.EDITS, "amber", sentence)
    if scoring_failed:
        return Strip(S.SCORING_FAILED, "red", "Scoring failed; the tree card says why.")
    if status is None and (has_result or loading):
        return Strip(S.WAITING, "grey", "Scoring…")
    if partial is not None and status in (None, NOT_RUN, SIM_FAIL):
        # Its last run did not finish and left a partial result (D151):
        # where it stopped, and that nothing of it is scored. A cancel is
        # nobody's failure; a timeout and a crash are the test's.
        if partial.how == CANCELLED:
            return Strip(S.CANCELLED, "grey", partial.sentence)
        why = _why(failure) if status == SIM_FAIL else ""
        return Strip(
            S.SIM_FAIL,
            "red",
            f"{partial.sentence}. {why} — {_FIX_IT}" if why else partial.sentence,
            tip=(failure or "") if why else "",
        )
    if queue is not None and queue.state is ItemState.CANCELLED:
        return Strip(S.CANCELLED, "grey", CANCELLED_SENTENCE)
    if queue is not None and queue.state is ItemState.FAILED and not queue.started:
        said = queue.message or ""
        return Strip(S.START_FAILED, "red", one_line(said) or queue.words, tip=said)
    # NOT RUN is the classifier's word for this situation (D145): the
    # header's badge and the strip say one thing. A SIM_FAIL is a run that
    # reached a verdict, and reads as one below.
    if status == NOT_RUN or (not has_result and status is None):
        return Strip(S.NO_RESULT, "grey", "No result yet — queue it to simulate it.")
    if status == SIM_FAIL:
        # The backend's own reason, not only that it failed: it was on the
        # queue's row alone, and Clear finished took it away.
        why = _why(failure)
        if why:
            return Strip(S.SIM_FAIL, "red", f"{why} — {_FIX_IT}", tip=failure or "")
        return Strip(
            S.SIM_FAIL,
            "red",
            f"The simulation failed. {_FIX_IT[0].upper()}{_FIX_IT[1:]}",
        )
    if refused and status == FAIL:
        # Before NEW_VARIABLES: a rerun records the variable, and the test
        # is refused all the same. The reason, never "accept it": accepting
        # keeps the reference's comparison settings, the refusal with them.
        # A reason that ends its own sentence (`… did you mean "max_value"?`)
        # is not given a second stop.
        stop = "" if refused.endswith(("?", "!", ".")) else "."
        return Strip(S.REFUSED, "red", f"Not scored: {refused}{stop}")
    if new_variables:
        return Strip(
            S.NEW_VARIABLES,
            "amber",
            f"{plural(new_variables, 'new variable')} the last run did not "
            f"record: {_queue_it(new_variables)}.",
        )
    if status == NO_REF:
        return Strip(
            S.NO_REF, "amber", "No baseline yet — accept this result as the first one."
        )
    if status == FAIL:
        if fails:
            what = f"Fails {fails} of {plural(criteria, 'criterion', 'criteria')}."
        else:
            what = "Fails against its baseline."
        return Strip(S.FAIL, "red", f"{what} {_ACCEPT_IT}")
    assert status == PASS, status
    if simulate_only:
        # The classifier's PASS for a test whose criterion is "it ran".
        return Strip(
            S.SIMULATE_ONLY, "grey", "Simulate-only: it runs, and nothing is compared."
        )
    if advisory:
        return Strip(
            S.PASS,
            "grey",
            f"Passes against its baseline, with {plural(advisory, 'advisory failure')}.",
        )
    return Strip(S.PASS, "grey", "Passes against its baseline.")
