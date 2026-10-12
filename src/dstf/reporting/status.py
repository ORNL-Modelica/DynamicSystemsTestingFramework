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
   with nothing stored. A test the comparison refused is here too, baseline
   or none (:func:`refusal`): one that scored NOTHING is not a pass.
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
from dataclasses import dataclass

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

#: A run stopped on request (the simulation queue's cancel, D150). It is
#: recorded as cancelled and read back as NO result, so the classifier's
#: word for the test is NOT RUN; this is the word a live row says for why
#: (:func:`scored_status` is where a row keeps it).
#: It wears NOT RUN's class: the pills, the tint and the exit code count it
#: there — nothing ran to a verdict, so nothing failed (D145).
CANCELLED = "CANCELLED"

#: What the tool says for a test whose scoring raised: it has a result and
#: no comparison at all. Not a status of the classifier's — it has no
#: comparison to classify — and the Tests drawer counts it under SIM_FAIL.
SCORING_FAILED = "scoring failed"

#: What the tool says for a test its Session has not scored yet — every
#: row while a project opens — and the class it wears: the neutral grey,
#: under a name of its own. NOT ``QUEUED``: since D150 that word is a test
#: in the simulation queue (and a waiting test of a CLI run), and a Suite
#: pill reading ``QUEUED 331`` beside a header reading ``idle`` said two
#: things at once. No status of the classifier's, and no status.json row
#: says it: the dashboard has no pill for it.
SCORING = "SCORING"
SCORING_CLASS = "scoring"

#: Every status, in the order a sort by status uses.
STATUS_ORDER = (
    QUEUED,
    RUNNING,
    PASS,
    FAIL,
    SIM_FAIL,
    NO_REF,
    TIMED_OUT,
    NOT_RUN,
    CANCELLED,
)

#: The statuses the count pills are drawn from, in their order: one per
#: class. CANCELLED has none of its own — it is counted under NOT RUN's.
PILL_STATUSES = tuple(s for s in STATUS_ORDER if s != CANCELLED)

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
    CANCELLED: "not-run",
}

#: Status → the word a person reads. Only TIMED_OUT differs from its key.
STATUS_LABELS = {s: s for s in STATUS_ORDER} | {TIMED_OUT: "TIMED OUT"}

_LIVE = {
    "queued": QUEUED,
    "running": RUNNING,
    "timed_out": TIMED_OUT,
    "failed": SIM_FAIL,
    "cancelled": CANCELLED,
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


def refusal(comp: TestComparison) -> str | None:
    """Why the comparison REFUSED this test — FAILED it without scoring a
    variable (a base tolerance that is no number; a result that holds
    nothing the test tracks) — or ``None``. The reason
    is the whole finding: there is no failing variable to name and no tree
    to draw, so every surface says it where it would name those. Not a
    SIM_FAIL's or a NO_REF's message, which their own status says."""
    if classify(comp) == FAIL and not comp.variables and comp.error_message:
        return comp.error_message
    return None


def scored_status(verdict: str, *, cancelled: bool) -> str:
    """What a ROW reads once its test has a verdict: *verdict*, the
    classifier's — but a run stopped on request keeps its word. A cancelled
    run scores NOT RUN, there being no result, and CANCELLED says why
    (D150); any other verdict means the test has a result since, and
    replaces the word as it replaces every live state.

    The one place this is decided. *cancelled* is what the caller's row
    knows — a status.json row's state, a queue item's — and every surface
    that puts a verdict on such a row asks here: the sidecar's enrichment
    (``rows.enrich_row``), the dashboard's comparison in hand and the
    Suite's row. A surface with no row (the console, JUnit) prints the
    verdict itself.
    """
    return CANCELLED if cancelled and verdict == NOT_RUN else verdict


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


def group_failures(comp: TestComparison) -> list[str]:
    """One line per group of the test's tree that fails on its own
    (``tree_eval.failed_groups``), in tree order: which group, and its score
    against its threshold — ``weighted group at metrics.children[1]:
    weighted sum 2.500000e-03 is not below its threshold 0.001``. A test
    that fails only there has no failing criterion, and JUnit said ``0
    criteria failed`` over an empty body. Imported late, as below."""
    from ..comparison.tree_eval import failed_groups

    lines = []
    for place, node in failed_groups(comp.metric_tree):
        kind = node.label.split("[", 1)[0]
        diag = node.diagnostics or {}
        if "weighted_sum" in diag:
            side = "below" if diag.get("direction", "less") == "less" else "above"
            why = (
                f"weighted sum {diag['weighted_sum']:.6e} is not {side} its "
                f"threshold {diag['threshold']:g}"
            )
        else:
            why = str(diag.get("reason") or "failed with no failing criterion under it")
        lines.append(f"{kind} group at {place}: {why}")
    return lines


def _window_text(window: object) -> str:
    """``window 0–0.5 s`` for a leaf scoped to part of the run; ``""`` for
    one that is not."""
    if not isinstance(window, dict):
        return ""
    start, end = window.get("start"), window.get("end")
    if start is None and end is None:
        return ""
    if start is None:
        return f"window to {end:g} s"
    if end is None:
        return f"window from {start:g} s"
    return f"window {start:g}–{end:g} s"


@dataclass(frozen=True)
class FailedCriterion:
    """One criterion that did not pass, in the words a text surface prints
    for it — the console as a block, JUnit as a line."""

    #: Which criterion it is: the variable (``pipe.T[1] (unitTests.x[1])``,
    #: the name alone when they agree — the expression says WHICH quantity,
    #: the name what to write a criterion or an override against), then its
    #: metric, its window if it has one and the baseline it is scored
    #: against; led by ``advisory — `` under a ``warn``, where it cannot
    #: fail the test.
    head: str
    advisory: bool
    #: The criterion in the metric's own words (the measured value against
    #: its tolerance or bound — ``registry.summarize``, what the report's
    #: tree row reads); ``""`` for one that was not scored.
    sentence: str
    #: The metric's own detail pairs; none for one that was not scored.
    details: tuple[tuple[str, str], ...]
    #: Why it was not scored (:func:`not_scored_reason`), else ``None``.
    reason: str | None

    @property
    def line(self) -> str:
        """The criterion on ONE line: its head, then its sentence or the
        reason it was not scored."""
        said = f"Not scored: {self.reason}" if self.reason else self.sentence
        return f"{self.head} — {said}"


def failed_criteria(comp: TestComparison) -> list[FailedCriterion]:
    """The criteria of ``comp`` that did not pass, in TREE order — two
    criteria on one variable are two entries, and each says which it is.
    THE one reading of a failing criterion for a surface with no page: the
    console's failure blocks and JUnit's failure lines are both written from
    it, so they cannot name a criterion differently. (JUnit wrote every
    metric's number as ``x: NRMSE=…``, and two failing criteria on one
    variable as two identical lines.) Imported late, as below."""
    from ..backends.types import label_with_name
    from ..comparison.registry import summarize
    from ..comparison.tree_eval import (
        advisory_comparisons,
        flatten_evaluation,
        leaf_comparisons_by_path,
    )

    tree = comp.metric_tree
    leaves: list[tuple[VariableComparison, dict]]
    if tree is not None:
        said = flatten_evaluation(tree)
        leaves = [
            (vc, said.get(path, {}))
            for path, vc in leaf_comparisons_by_path(tree).items()
        ]
    else:
        leaves = [(vc, {}) for vc in comp.variables]
    advisory = {id(vc) for vc in advisory_comparisons(tree)}
    out: list[FailedCriterion] = []
    for vc, leaf in leaves:
        if vc.passed:
            continue
        name = label_with_name(vc.name, vc.expression)
        # The metric is the comparison's own `mode`, as on every surface;
        # the one criterion with none (a flat override naming a mode
        # nothing is registered under) is headed by its variable alone.
        what = [vc.mode] if vc.mode else []
        window = _window_text(leaf.get("window"))
        if window:
            what.append(window)
        if leaf.get("against"):
            what.append(f"against {leaf['against']}")
        under_warn = id(vc) in advisory
        lead = "advisory — " if under_warn else ""
        head = f"{lead}{': '.join([name, ' · '.join(what)] if what else [name])}"
        reason = not_scored_reason(vc)
        summary = None if reason else summarize(vc)
        out.append(
            FailedCriterion(
                head=head,
                advisory=under_warn,
                sentence=summary.criterion if summary is not None else "",
                details=tuple(summary.details) if summary is not None else (),
                reason=reason,
            )
        )
    return out


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
