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

from dataclasses import dataclass
from enum import Enum

from ..reporting.status import FAIL, NO_REF, PASS, SIM_FAIL
from .capabilities import plural


class Situation(Enum):
    RUNNING = "running"
    EDITS = "edits"
    SCORING_FAILED = "scoring_failed"
    WAITING = "waiting"
    NO_RESULT = "no_result"
    SIM_FAIL = "sim_fail"
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


def _verdict(status: str | None, saved_status: str | None) -> str:
    """The EDITS clause after the count: what the edits do to the verdict."""
    if saved_status is None:
        return f"a new entry, scored {status or '…'}"
    if status == saved_status:
        return f"verdict stays {status}"
    return f"verdict would change {saved_status} → {status or '…'}"


def strip_for(
    *,
    running: str | None = None,
    edits: int = 0,
    status: str | None = None,
    saved_status: str | None = None,
    has_result: bool = False,
    scoring_failed: bool = False,
    simulate_only: bool = False,
    fails: int = 0,
    criteria: int = 0,
    advisory: int = 0,
) -> Strip:
    """The strip for one test. ``running`` is the label of the job that
    locks it; ``edits`` its pending ops; ``status`` / ``saved_status`` the
    classifier's word for the comparison as edited / as saved (None while
    unscored, or — saved — for an entry the loaded spec does not have);
    ``has_result`` whether the last run finished it; ``fails`` /
    ``criteria`` / ``advisory`` its leaves failing, in all, and failing
    inside a warn."""
    S = Situation
    if running is not None:
        return Strip(S.RUNNING, "grey", f"{running} is running on this test.")
    if edits:
        return Strip(
            S.EDITS,
            "amber",
            f"{plural(edits, 'unsaved edit')} · {_verdict(status, saved_status)}",
        )
    if scoring_failed:
        return Strip(S.SCORING_FAILED, "red", "Scoring failed; the tree card says why.")
    if status is None and has_result:
        return Strip(S.WAITING, "grey", "Scoring…")
    if not has_result and status in (None, SIM_FAIL):
        return Strip(S.NO_RESULT, "grey", "No result yet — rerun to simulate it.")
    if status == SIM_FAIL:
        return Strip(
            S.SIM_FAIL,
            "red",
            "The simulation failed. Fix the model or its settings, then rerun.",
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
