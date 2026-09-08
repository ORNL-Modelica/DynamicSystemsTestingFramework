"""The per-test status classification every report surface agrees on.

One classifier, one ordering. The same ordering bug had to be fixed twice
in separate copies of it (review 2026-07-06: finding 5 — a failing
baseline-free test read NO_REF and exited 0; finding 69 — NO_REF was
counted as passed and SIM_FAIL as failed), which is what this module
exists to make impossible.

Ordering, and why:

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

from ..comparison.types import TestComparison, VariableComparison

PASS = "PASS"
FAIL = "FAIL"
SIM_FAIL = "SIM_FAIL"
NO_REF = "NO_REF"

#: Status → the CSS class the HTML surfaces (dashboard row, per-test
#: report header) style it with. Single table so the two renderers cannot
#: drift into different names for the same verdict.
STATUS_CLASSES = {
    PASS: "pass",
    FAIL: "fail",
    SIM_FAIL: "sim-fail",
    NO_REF: "no-ref",
}


def classify(comp: TestComparison) -> str:
    """Return this test's status: one of PASS / FAIL / SIM_FAIL / NO_REF."""
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
    """Count *comparisons* into the four mutually-exclusive status buckets.

    Missing buckets read 0 (``Counter``), so callers can index all four
    without guarding.
    """
    return Counter(classify(c) for c in comparisons)


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
