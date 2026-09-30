"""The per-test status classification every report surface agrees on.

One classifier, one ordering. The same ordering bug had to be fixed twice
in separate copies of it (review 2026-07-06: finding 5 — a failing
baseline-free test read NO_REF and exited 0; finding 69 — NO_REF was
counted as passed and SIM_FAIL as failed), which is what this module
exists to make impossible.

Ordering, and why:

0. ``NOT RUN`` — there is no result: the last run never ran the test, or
   never finished it (D145). Nothing ran, so nothing failed, and no verdict
   about the trajectories exists. (A run in which NO test ran still exits
   1: ``exit_code``.)
1. ``SIM_FAIL`` — the simulation never produced data, so no verdict
   about the trajectories is meaningful.
2. ``FAIL`` — a failing verdict outranks a missing baseline: baseline-free
   modes (``range``, ``points`` with explicit values) carry a real verdict
   with nothing stored.
3. simulate-only — the pass criterion IS "it simulated", so a missing
   baseline is irrelevant and the verdict is PASS, never NO_REF.
4. ``NO_REF`` — reserved for tests where nothing was evaluated at all.
5. ``PASS``.

The buckets are mutually exclusive, so header counts always reconcile
with row statuses.

D117 left the report suite with a private copy of this ordering because
it had the ``TestModel`` in hand and this function did not: simulate-only
tests were the one verdict the two surfaces disagreed on. ``TestComparison``
now carries ``simulate_only`` itself, so the input asymmetry — and the
copy — are gone. Every surface classifies here.
"""

from collections import Counter
from collections.abc import Iterable

from ..comparison.types import TestComparison, VariableComparison

PASS = "PASS"
FAIL = "FAIL"
SIM_FAIL = "SIM_FAIL"
NO_REF = "NO_REF"
#: No result exists (D145): no manifest entry, or one the last run left
#: ``pending``. The key is the word a person reads, so a surface that shows
#: the classifier's key reads it. Not a failure (``FAILURES``).
NOT_RUN = "NOT RUN"

#: The statuses that fail a run: what ``compare`` and ``run`` exit on
#: (``exit_code``), beside a run in which nothing ran. NO_REF and NOT RUN
#: are not in it.
FAILURES = frozenset({FAIL, SIM_FAIL})

#: The states a test is in before it has a verdict, from a live run's
#: status.json. A test that simulated and is not yet scored has no word of
#: its own: the dashboard shows it as its live row says until the compare
#: phase writes its verdict.
QUEUED = "QUEUED"
RUNNING = "RUNNING"
TIMED_OUT = "TIMED_OUT"

#: What the tool says for a test whose scoring raised: it has a result and
#: no comparison at all. Not a status of the classifier's — it has no
#: comparison to classify — and the Tests drawer counts it under SIM_FAIL.
SCORING_FAILED = "scoring failed"

#: Every status, in the order the count pills and a sort by status use.
STATUS_ORDER = (QUEUED, RUNNING, PASS, FAIL, SIM_FAIL, NO_REF, TIMED_OUT, NOT_RUN)

#: Status → the CSS class both HTML surfaces style it with. One table, so
#: the dashboard, the report header and the tool cannot drift into two
#: names for one state.
STATUS_CLASSES = {
    PASS: "pass",
    FAIL: "fail",
    SIM_FAIL: "sim-fail",
    NO_REF: "no-ref",
    QUEUED: "queued",
    RUNNING: "running",
    TIMED_OUT: "timed-out",
    NOT_RUN: "not-run",
}

#: Status → the word a person reads. Only TIMED_OUT differs from its key.
STATUS_LABELS = {s: s for s in STATUS_ORDER} | {TIMED_OUT: "TIMED OUT"}

_LIVE = {
    "queued": QUEUED,
    "running": RUNNING,
    "timed_out": TIMED_OUT,
    "failed": SIM_FAIL,
}


#: Every state a status.json entry is in: the ones above, and "passed" —
#: the simulation ran, and the verdict is the compare phase's.
LIVE_STATES = frozenset(_LIVE) | {"passed"}


def live_status(raw: str) -> str | None:
    """A live status.json state as a status, or None for "passed": the
    simulation ran and the verdict is the compare phase's to give."""
    return _LIVE.get(raw)


def classify(comp: TestComparison) -> str:
    """Return this test's status: NOT RUN / PASS / FAIL / SIM_FAIL / NO_REF."""
    if not comp.ran:
        return NOT_RUN
    if not comp.sim_success:
        return SIM_FAIL
    if not comp.passed:
        return FAIL
    if comp.simulate_only:
        # Pass criterion is "the simulation ran"; a baseline is not part
        # of it, so this must not fall through to NO_REF.
        return PASS
    if not comp.has_reference and not comp.evaluated:
        return NO_REF
    return PASS


def classify_with_class(comp: TestComparison) -> tuple[str, str]:
    """Return ``(status_text, css_class)`` for the HTML surfaces."""
    state = classify(comp)
    return state, STATUS_CLASSES[state]


def count_by_status(comparisons: list[TestComparison]) -> Counter[str]:
    """Count *comparisons* into the mutually-exclusive status buckets.

    Missing buckets read 0 (``Counter``), so callers can index every one
    without guarding.
    """
    return Counter(classify(c) for c in comparisons)


def nothing_ran(comparisons: Iterable[TestComparison]) -> bool:
    """True when no test ran: no comparisons at all, or every one NOT RUN.

    A compare over an empty or wrong work dir, or after a run whose worker
    pool never started (every test stamped pending), is here. That is not a
    green run, though no test in it failed (D145 §5)."""
    return not any(c.ran for c in comparisons)


def exit_code(comparisons: Iterable[TestComparison]) -> int:
    """1 when any test FAILED or failed to simulate, or when nothing ran
    (:func:`nothing_ran`), else 0: a test NOT RUN beside tests that ran, or
    one with no baseline, does not fail a run (D145)."""
    comps = list(comparisons)
    if nothing_ran(comps):
        return 1
    return 1 if any(classify(c) in FAILURES for c in comps) else 0


def not_scored_reason(var: VariableComparison) -> str | None:
    """Why a leaf produced no score, or ``None`` when it scored one.

    Sentinels (a metric config that could not build, a baseline that is
    not there, a quantity path absent from one side, no data to compare)
    carry every number as inf/nan; the reason is the whole finding, and
    every surface must print it in place of that wall. It rides in
    ``diagnostics["error"]`` — the one exception is a scalar sentinel
    whose summary states the reason without a diagnostic, so the mode's
    own summary is the fallback. Imported late: ``registry`` reaches back
    into ``comparison`` and this module is imported from the CLI.
    """
    error = (var.diagnostics or {}).get("error")
    if error:
        return str(error)
    from ..comparison.registry import summarize

    summary = summarize(var)
    if summary.display == "not scored":
        return summary.criterion
    return None
