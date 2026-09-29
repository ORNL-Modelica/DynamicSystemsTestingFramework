"""A test's situation, as the Test page's status strip says it: what state
the test is in, the tone the strip is drawn in, and the one sentence that
says it. Pure — no Session, no UI: the page reads the inputs, this decides.

The situations are checked in :class:`Situation`'s order; the first that
applies wins (a running job before pending edits, edits before anything
the last scoring said).

There is no state for edits to the simulation settings (the owner's ruling
Q2 A drew one): ``/simulation`` is not stageable — `patch_apply`'s default
whitelist is ``/comparison``, ``/metrics`` and ``/plots``, and widening it
changes ``spec-update``, a seam another repository relies on. It waits for
a simulation-settings editor."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from ..comparison.tree_spec import tree_variables
from ..reporting.status import FAIL, NO_REF, PASS, SIM_FAIL
from .capabilities import plural

if TYPE_CHECKING:
    from ..backends.types import TestResult
    from ..discovery.test_registry import TestModel


class Situation(Enum):
    RUNNING = "running"
    EDITS = "edits"
    #: pending edits whose criteria name a variable the last run did not
    #: record: Rerun is the one next action, Save is quiet (the owner's O1)
    EDITS_NEW = "edits_new"
    SCORING_FAILED = "scoring_failed"
    WAITING = "waiting"
    NO_RESULT = "no_result"
    SIM_FAIL = "sim_fail"
    #: saved criteria name a variable the last run did not record: a
    #: baseline accepted now would not hold it (the owner's O3)
    NEW_VARIABLES = "new_variables"
    NO_REF = "no_ref"
    FAIL = "fail"
    SIMULATE_ONLY = "simulate_only"
    PASS = "pass"


@dataclass(frozen=True)
class Strip:
    situation: Situation
    tone: str  # "grey" | "red" | "amber"
    sentence: str


_ACCEPT_IT = "If the new behaviour is right, accept it as the baseline."

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
    a result: there is nothing to rerun FOR yet."""
    if test is None or result is None:
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
    it (the owner's O2), so the result holds it by the time this counts."""
    return len(unrecorded_variables(test, result, diagnostics))


def _rerun(n: int) -> str:
    return f"rerun to score {'it' if n == 1 else 'them'}"


def strip_for(
    *,
    running: str | None = None,
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
) -> Strip:
    """The strip for one test. ``running`` is the label of the job that
    locks it; ``edits`` its pending ops; ``status`` / ``saved_status`` the
    classifier's word for the comparison as edited / as saved (None while
    unscored, or — saved — for an entry the loaded spec does not have;
    ``reporting.status.SCORING_FAILED`` — saved — when scoring the saved
    spec raised);
    ``has_result`` whether the last run finished it; ``loading`` whether
    the project is still being read — an unscored test is then waiting for
    it, never without a result, which may not be read yet; ``fails`` /
    ``criteria`` / ``advisory`` its leaves failing, in all, and failing
    inside a warn; ``new_variables`` the variables its criteria name that
    the last run did not record (:func:`new_variables`)."""
    S = Situation
    if running is not None:
        return Strip(S.RUNNING, "grey", f"{running} is running on this test.")
    if edits:
        sentence = f"{plural(edits, 'unsaved edit')}{SEP}{verdict_phrase(status, saved_status)}"
        if new_variables:
            new = f"{plural(new_variables, 'new variable')}: {_rerun(new_variables)}"
            return Strip(S.EDITS_NEW, "amber", f"{sentence}{SEP}{new}")
        return Strip(S.EDITS, "amber", sentence)
    if scoring_failed:
        return Strip(S.SCORING_FAILED, "red", "Scoring failed; the tree card says why.")
    if status is None and (has_result or loading):
        return Strip(S.WAITING, "grey", "Scoring…")
    if not has_result and status in (None, SIM_FAIL):
        return Strip(S.NO_RESULT, "grey", "No result yet — rerun to simulate it.")
    if status == SIM_FAIL:
        return Strip(
            S.SIM_FAIL,
            "red",
            "The simulation failed. Fix the model or its settings, then rerun.",
        )
    if new_variables:
        return Strip(
            S.NEW_VARIABLES,
            "amber",
            f"{plural(new_variables, 'new variable')} the last run did not "
            f"record: {_rerun(new_variables)}.",
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
