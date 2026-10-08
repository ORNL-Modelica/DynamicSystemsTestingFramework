"""A test's situation and the strip's sentence (the Test page's status strip)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from dstf.authoring.queue_rows import QueueRow
from dstf.authoring.situation import CANCELLED_SENTENCE, Situation, Strip, strip_for
from dstf.backends.pool import ItemState

S = Situation

# The test's row in the simulation queue (`queue_rows.queue_row`), one per
# state an item passes through.
QUEUED = QueueRow(
    number=4,
    model_id="L.A",
    state=ItemState.WAITING,
    words="waiting · 3 ahead",
    status="QUEUED",
    ahead=3,
)
RUNNING = QueueRow(
    number=1,
    model_id="L.A",
    state=ItemState.RUNNING,
    words="running · 42 s",
    status="RUNNING",
    worker=2,
    elapsed=42.4,
    started=True,
)
FINISHED = QueueRow(
    number=1,
    model_id="L.A",
    state=ItemState.FINISHED,
    words="finished · 9 s",
    status="PASS",
    elapsed=9.0,
    started=True,
)
CANCELLED = QueueRow(
    number=1,
    model_id="L.A",
    state=ItemState.CANCELLED,
    words="cancelled",
    status="CANCELLED",
    started=True,
)
# A simulation that raised: a worker took it.
SIM_FAILED = QueueRow(
    number=1,
    model_id="L.A",
    state=ItemState.FAILED,
    words="failed: boom in simulate",
    status="SIM_FAIL",
    message="boom in simulate",
    started=True,
)
NO_WORKER = "No Stub worker could start — worker 0: licence server unreachable"
# No worker could start: no worker took it, and its last result stands.
START_FAILED = QueueRow(
    number=1,
    model_id="L.A",
    state=ItemState.FAILED,
    words=f"failed: {NO_WORKER}",
    status="NOT RUN",
    message=NO_WORKER,
    started=False,
)

ROWS = [
    (
        dict(queue=QUEUED, status="FAIL", has_result=True),
        S.QUEUED,
        "grey",
        "Queued — waiting · 3 ahead",
    ),
    (
        dict(queue=replace(QUEUED, words="waiting · next", ahead=0), status="PASS"),
        S.QUEUED,
        "grey",
        "Queued — waiting · next",
    ),
    # A running test has no result: its run dropped the last one.
    (
        dict(queue=RUNNING, status="NOT RUN"),
        S.RUNNING,
        "grey",
        "Running on worker 2 — 42 s",
    ),
    (
        dict(queue=replace(RUNNING, worker=None, elapsed=None), status="NOT RUN"),
        S.RUNNING,
        "grey",
        "Running — 0 s",
    ),
    (
        dict(edits=2, status="FAIL", saved_status="PASS"),
        S.EDITS,
        "amber",
        "2 unsaved edits · verdict would change PASS → FAIL",
    ),
    (
        dict(edits=1, status="PASS", saved_status="PASS"),
        S.EDITS,
        "amber",
        "1 unsaved edit · verdict stays PASS",
    ),
    (
        dict(edits=1, status="PASS", saved_status=None),
        S.EDITS,
        "amber",
        "1 unsaved edit · a new entry, scored PASS",
    ),
    (
        dict(scoring_failed=True),
        S.SCORING_FAILED,
        "red",
        "Scoring failed; the tree card says why.",
    ),
    (dict(status=None, has_result=True), S.WAITING, "grey", "Scoring…"),
    # While the project is still being read an unscored test is waiting for
    # it, never without a result: its result may not be read yet.
    (dict(status=None, has_result=False, loading=True), S.WAITING, "grey", "Scoring…"),
    # Its last run in the queue was cancelled: the run dropped the test's
    # result when it started, and the strip says so.
    (
        dict(queue=CANCELLED, status="NOT RUN", has_result=False),
        S.CANCELLED,
        "grey",
        "Cancelled — no result (its earlier result was replaced when the run started).",
    ),
    # No worker could start: the backend's message, whatever the last
    # result — which stands — reads.
    (
        dict(queue=START_FAILED, status="PASS", has_result=True),
        S.START_FAILED,
        "red",
        NO_WORKER,
    ),
    (
        dict(queue=START_FAILED, status="NOT RUN", has_result=False),
        S.START_FAILED,
        "red",
        NO_WORKER,
    ),
    # A row without the message says its words.
    (
        dict(queue=replace(START_FAILED, message=None), status="NOT RUN"),
        S.START_FAILED,
        "red",
        f"failed: {NO_WORKER}",
    ),
    (
        dict(status=None, has_result=False),
        S.NO_RESULT,
        "grey",
        "No result yet — queue it to simulate it.",
    ),
    # D145: the classifier's NOT RUN is this strip's "No result yet", and
    # only it is: the header's badge and the strip say one thing.
    (
        dict(status="NOT RUN", has_result=False),
        S.NO_RESULT,
        "grey",
        "No result yet — queue it to simulate it.",
    ),
    (
        dict(status="NOT RUN", has_result=True),
        S.NO_RESULT,
        "grey",
        "No result yet — queue it to simulate it.",
    ),
    # The classifier's SIM_FAIL is a run that reached a verdict (D145): the
    # strip says it failed, as the badge does, whatever result is held.
    (
        dict(status="SIM_FAIL", has_result=False),
        S.SIM_FAIL,
        "red",
        "The simulation failed. Fix the model or its settings, then queue it again.",
    ),
    (
        dict(status="SIM_FAIL", has_result=True),
        S.SIM_FAIL,
        "red",
        "The simulation failed. Fix the model or its settings, then queue it again.",
    ),
    (
        dict(status="NO_REF", has_result=True),
        S.NO_REF,
        "amber",
        "No baseline yet — accept this result as the first one.",
    ),
    (
        dict(status="FAIL", has_result=True, fails=1, criteria=3),
        S.FAIL,
        "red",
        "Fails 1 of 3 criteria. If the new behaviour is right, accept it as the baseline.",
    ),
    (
        # A FAIL none of whose criteria fails on its own (a weighted sum
        # over its threshold) names no count, however many criteria it has.
        dict(status="FAIL", has_result=True, fails=0, criteria=3),
        S.FAIL,
        "red",
        "Fails against its baseline. If the new behaviour is right, accept it as the baseline.",
    ),
    (
        dict(status="FAIL", has_result=True, fails=0),
        S.FAIL,
        "red",
        "Fails against its baseline. If the new behaviour is right, accept it as the baseline.",
    ),
    (
        # A test the comparison refused: the reason is the sentence, and
        # accepting is not offered — it would not clear it.
        dict(status="FAIL", has_result=True, refused="the base is a pair"),
        S.REFUSED,
        "red",
        "Not scored: the base is a pair.",
    ),
    (
        dict(status="PASS", has_result=True, simulate_only=True),
        S.SIMULATE_ONLY,
        "grey",
        "Simulate-only: it runs, and nothing is compared.",
    ),
    (
        dict(status="PASS", has_result=True),
        S.PASS,
        "grey",
        "Passes against its baseline.",
    ),
    (
        dict(status="PASS", has_result=True, advisory=1),
        S.PASS,
        "grey",
        "Passes against its baseline, with 1 advisory failure.",
    ),
    (
        dict(status="PASS", has_result=True, advisory=2),
        S.PASS,
        "grey",
        "Passes against its baseline, with 2 advisory failures.",
    ),
]


@pytest.mark.parametrize(("inputs", "situation", "tone", "sentence"), ROWS)
def test_each_situation_has_its_tone_and_sentence(inputs, situation, tone, sentence):
    assert strip_for(**inputs) == Strip(situation, tone, sentence)


def test_the_values_are_the_lower_case_names():
    assert all(s.value == s.name.lower() for s in Situation)


def test_a_strip_is_frozen():
    strip = strip_for(status="PASS", has_result=True)
    with pytest.raises(AttributeError):
        strip.tone = "red"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("inputs", "first"),
    [
        # a row's inputs plus a later row's trigger: the earlier wins
        (dict(queue=QUEUED, edits=2, status="FAIL"), S.QUEUED),
        (dict(queue=QUEUED, scoring_failed=True), S.QUEUED),
        (dict(queue=RUNNING, edits=2, status="NOT RUN"), S.RUNNING),
        (dict(queue=RUNNING, scoring_failed=True), S.RUNNING),
        (dict(queue=RUNNING, status=None, loading=True), S.RUNNING),
        (
            dict(edits=1, status="FAIL", saved_status="PASS", scoring_failed=True),
            S.EDITS,
        ),
        (dict(scoring_failed=True, status=None, has_result=True), S.SCORING_FAILED),
        (dict(scoring_failed=True, status="PASS", has_result=True), S.SCORING_FAILED),
        (dict(edits=1, status="PASS", simulate_only=True, has_result=True), S.EDITS),
        (
            dict(status="SIM_FAIL", has_result=True, simulate_only=True),
            S.SIM_FAIL,
        ),
    ],
)
def test_precedence_is_the_tables_order(inputs, first):
    assert strip_for(**inputs).situation is first


@pytest.mark.parametrize(
    "ended", [CANCELLED, START_FAILED], ids=["cancelled", "start-failed"]
)
def test_an_ended_item_speaks_after_the_edits_and_before_no_result(ended):
    """A test the queue holds is said first; one whose item ended cancelled
    or unstarted is said after its edits, a scoring that failed and
    "Scoring…", and before "No result yet"."""
    own = S.CANCELLED if ended is CANCELLED else S.START_FAILED
    cases = [
        (dict(edits=1, status="NOT RUN", saved_status="NOT RUN"), S.EDITS),
        (dict(edits=1, status="NOT RUN", new_variables=1), S.EDITS_NEW),
        (dict(scoring_failed=True), S.SCORING_FAILED),
        (dict(status=None, has_result=True), S.WAITING),
        (dict(status=None, has_result=False, loading=True), S.WAITING),
        (dict(status="NOT RUN", has_result=False), own),
        (dict(status=None, has_result=False), own),
    ]
    for inputs, first in cases:
        assert strip_for(queue=ended, **inputs).situation is first, inputs


def test_the_cancelled_strip_says_the_modules_sentence():
    assert strip_for(queue=CANCELLED, status="NOT RUN").sentence == CANCELLED_SENTENCE


@pytest.mark.parametrize(
    ("inputs", "situation"),
    [
        # An item that finished says nothing of its own: the verdict does.
        (dict(queue=FINISHED, status="PASS", has_result=True), S.PASS),
        (dict(queue=FINISHED, status="FAIL", has_result=True, fails=1), S.FAIL),
        # A simulation that raised started: it is the classifier's SIM_FAIL,
        # never "could not start".
        (dict(queue=SIM_FAILED, status="SIM_FAIL", has_result=True), S.SIM_FAIL),
    ],
)
def test_an_item_that_ran_to_its_end_reads_as_its_verdict(inputs, situation):
    with_row = strip_for(**inputs)
    assert with_row.situation is situation
    assert with_row == strip_for(**{k: v for k, v in inputs.items() if k != "queue"})


# -- a criterion on a variable the last run did not record ------------------

NEW_ROWS = [
    (
        dict(
            edits=1,
            status="FAIL",
            saved_status="PASS",
            has_result=True,
            new_variables=1,
        ),
        S.EDITS_NEW,
        "amber",
        "1 unsaved edit · verdict would change PASS → FAIL"
        " · 1 new variable: queue the test to score it",
    ),
    (
        dict(
            edits=2,
            status="FAIL",
            saved_status="FAIL",
            has_result=True,
            new_variables=2,
        ),
        S.EDITS_NEW,
        "amber",
        "2 unsaved edits · verdict stays FAIL"
        " · 2 new variables: queue the test to score them",
    ),
    # Saved without a new run: the leaf still reads "not found in the result",
    # and "accept it" would store a baseline without the variable.
    (
        dict(status="FAIL", has_result=True, fails=1, criteria=2, new_variables=1),
        S.NEW_VARIABLES,
        "amber",
        "1 new variable the last run did not record: queue the test to score it.",
    ),
    (
        dict(status="NO_REF", has_result=True, new_variables=2),
        S.NEW_VARIABLES,
        "amber",
        "2 new variables the last run did not record: queue the test to score them.",
    ),
]


@pytest.mark.parametrize(("inputs", "situation", "tone", "sentence"), NEW_ROWS)
def test_new_variables_ask_for_the_test_to_be_queued(inputs, situation, tone, sentence):
    assert strip_for(**inputs) == Strip(situation, tone, sentence)


def test_nothing_new_is_todays_sentence():
    for inputs, situation, tone, sentence in ROWS:
        assert strip_for(**inputs, new_variables=0) == Strip(situation, tone, sentence)


@pytest.mark.parametrize(
    ("inputs", "first"),
    [
        (dict(queue=QUEUED, edits=1, new_variables=1), S.QUEUED),
        (dict(queue=RUNNING, edits=1, new_variables=1), S.RUNNING),
        (dict(status="SIM_FAIL", has_result=True, new_variables=1), S.SIM_FAIL),
        (dict(status=None, has_result=False, new_variables=1), S.NO_RESULT),
        (dict(status=None, has_result=True, new_variables=1), S.WAITING),
        (dict(scoring_failed=True, new_variables=1), S.SCORING_FAILED),
        (dict(status="PASS", has_result=True, new_variables=1), S.NEW_VARIABLES),
        # A new run records the variable and the test is refused all the same.
        (
            dict(status="FAIL", has_result=True, refused="why", new_variables=1),
            S.REFUSED,
        ),
    ],
)
def test_new_variables_come_after_sim_fail_and_before_no_ref(inputs, first):
    assert strip_for(**inputs).situation is first


def test_new_variables_counts_what_the_result_lacks():
    import numpy as np

    from conftest import make_test_model
    from dstf.authoring.situation import new_variables
    from dstf.backends.types import TestResult, VariableResult
    from dstf.comparison.tree_spec import parse_metric_tree

    test = make_test_model(
        "L.A",
        variable_patterns=["x"],
        metric_tree_spec=parse_metric_tree(
            {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": "x"},
                    {"metric": "range", "variable": "y", "max_value": 1},
                    {"metric": "scalar", "variable": "c", "quantity": "CPUtime.final"},
                    {
                        "metric": "scalar",
                        "variable": "CPUtime",
                        "quantity": {"reduce": "max"},
                    },
                ],
            }
        ),
    )
    t = np.array([0.0, 1.0])
    result = TestResult(
        model_id="L.A",
        success=True,
        variables=[VariableResult(index=1, time=t, values=t, name="x")],
    )
    assert new_variables(test, result, ["CPUtime"]) == 1
    assert new_variables(test, None, ["CPUtime"]) == 0
    assert new_variables(None, result, ["CPUtime"]) == 0


def test_a_failed_result_counts_nothing_new():
    """A failed run's result holds no variables: without the guard every
    criterion's variable counted as new, and the strip asked for a new run to
    score them over a simulation that fails."""
    from conftest import make_test_model
    from dstf.authoring.situation import new_variables
    from dstf.backends.types import TestResult
    from dstf.comparison.tree_spec import parse_metric_tree

    test = make_test_model(
        "L.A",
        variable_patterns=["x"],
        metric_tree_spec=parse_metric_tree(
            {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": "x"},
                    {"metric": "nrmse", "variable": "y"},
                ],
            }
        ),
    )
    failed = TestResult(model_id="L.A", success=False, error_message="boom")
    assert new_variables(test, failed, []) == 0


def test_a_simulate_only_test_counts_nothing_new():
    """Nothing is ever scored on a simulate-only test, so no variable is
    one a new run would score: never NEW_VARIABLES, never EDITS_NEW."""
    from conftest import make_test_model
    from dstf.authoring.situation import new_variables
    from dstf.backends.types import TestResult
    from dstf.comparison.tree_spec import parse_metric_tree

    test = make_test_model(
        "L.A",
        simulate_only=True,
        metric_tree_spec=parse_metric_tree(
            {"metric": "range", "variable": "y", "max_value": 1}
        ),
    )
    result = TestResult(model_id="L.A", success=True, variables=[])
    assert new_variables(test, result, []) == 0


def test_a_refused_test_does_not_offer_accept_as_the_next_action():
    """Accepting keeps the reference JSON's ``comparison.tolerance``, so it
    would not clear a refusal that came from there."""
    from dstf.authoring.builtin.accept import Accept
    from dstf.authoring.builtin.enqueue import EnqueueTests

    assert Accept.emphasis.get(S.REFUSED) is None
    assert EnqueueTests.emphasis.get(S.REFUSED) is None
    # The sentence of a FAIL that was scored is the one that offers it.
    assert "accept" in strip_for(status="FAIL", has_result=True).sentence
    assert (
        "accept" not in strip_for(status="FAIL", has_result=True, refused="x").sentence
    )
