"""A test's situation and the strip's sentence (the Test page's status strip)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from dstf.authoring import live_rows
from dstf.authoring.live_rows import NOT_AVAILABLE, Partial
from dstf.authoring.queue_rows import RESTARTING, QueueRow
from dstf.authoring.situation import CANCELLED_SENTENCE, Situation, Strip, strip_for
from dstf.backends.pool import ItemState

S = Situation


def _said(strip: Strip) -> tuple[Situation, str, str]:
    """A strip's situation, tone and sentence — what the tables below pin;
    its note and its tip have tests of their own."""
    return strip.situation, strip.tone, strip.sentence


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
        # A reason that ends its own sentence gets no second stop.
        dict(
            status="FAIL",
            has_result=True,
            refused='ref_0002.json: unknown field "max_val" — did you mean "max_value"?',
        ),
        S.REFUSED,
        "red",
        'Not scored: ref_0002.json: unknown field "max_val" — did you mean "max_value"?',
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
    assert _said(strip_for(**inputs)) == (situation, tone, sentence)


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
    assert _said(strip_for(**inputs)) == (situation, tone, sentence)


def test_nothing_new_is_todays_sentence():
    for inputs, situation, tone, sentence in ROWS:
        assert _said(strip_for(**inputs, new_variables=0)) == (
            situation,
            tone,
            sentence,
        )


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
                    {
                        "metric": "scalar",
                        "variable": "c",
                        "quantity": "translation.continuous_time_states",
                    },
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


# -- a running test's file, and a run that did not finish (D151) -----------------


def test_a_running_strip_says_how_far_the_file_has_got_where_it_is_read():
    live = replace(RUNNING, progress="t = 42 / 100 s")
    strip = strip_for(queue=live, status="NOT RUN")
    assert _said(strip) == (S.RUNNING, "grey", "Running on worker 2 — t = 42 / 100 s")
    assert strip.note == ""
    starting = strip_for(queue=replace(RUNNING, progress="starting"))
    assert starting.sentence == "Running on worker 2 — starting"
    restarting = strip_for(queue=replace(RUNNING, progress=RESTARTING))
    assert restarting.sentence == "Running on worker 2 — worker restarting"
    assert restarting.note == ""


def test_a_running_strip_says_once_that_its_backend_has_no_live_view():
    strip = strip_for(queue=RUNNING, status="NOT RUN")
    assert strip.sentence == "Running on worker 2 — 42 s"
    assert strip.note == NOT_AVAILABLE
    assert strip_for(queue=QUEUED).note == ""


@pytest.mark.parametrize(
    ("how", "status", "situation", "tone"),
    [
        (live_rows.CANCELLED, "NOT RUN", S.CANCELLED, "grey"),
        (live_rows.TIMED_OUT, "SIM_FAIL", S.SIM_FAIL, "red"),
        (live_rows.FAILED, "SIM_FAIL", S.SIM_FAIL, "red"),
    ],
)
def test_a_partial_result_s_strip_says_where_it_stopped(how, status, situation, tone):
    partial = Partial(how, 19.2, 200.0)
    strip = strip_for(status=status, has_result=status == "SIM_FAIL", partial=partial)
    assert _said(strip) == (situation, tone, f"{how} at t = 19.2 of 200 s — not scored")
    # ... whether or not the queue still lists the run
    listed = strip_for(queue=CANCELLED, status=status, partial=partial)
    assert listed.sentence == strip.sentence


def test_a_partial_result_comes_after_the_queue_s_hold_and_pending_edits():
    partial = Partial(live_rows.CANCELLED, 19.2, 200.0)
    assert strip_for(queue=RUNNING, partial=partial).situation is S.RUNNING
    assert strip_for(edits=1, status="NOT RUN", partial=partial).situation is S.EDITS
    # ... and says nothing once the test has a result that was scored
    scored = strip_for(status="PASS", has_result=True, partial=partial)
    assert scored.situation is S.PASS


# -- a failed run's own reason ---------------------------------------------------

TIMEOUT = (
    "Timed out after 12s (raise it per test with simulation.timeout in "
    "test_spec.json, or globally with --timeout)"
)
CRASH = (
    "Python execution failed: Traceback (most recent call last):\n"
    '  File "A.py", line 3, in simulate\n'
    "RuntimeError: boom in simulate"
)


def test_a_failed_run_s_strip_says_the_backend_s_reason_then_the_next_action():
    """It was on the queue's row alone, which Clear finished removes: the
    strip reads the comparison's message, the stored run result's."""
    strip = strip_for(status="SIM_FAIL", has_result=True, failure=TIMEOUT)
    assert _said(strip) == (
        S.SIM_FAIL,
        "red",
        f"{TIMEOUT} — fix the model or its settings, then queue it again.",
    )
    assert strip.tip == TIMEOUT
    # with or without the queue's row
    assert strip_for(queue=SIM_FAILED, status="SIM_FAIL", failure=TIMEOUT) == strip


def test_a_long_reason_is_one_line_with_the_whole_of_it_on_hover():
    strip = strip_for(status="SIM_FAIL", has_result=True, failure=CRASH)
    assert strip.sentence == (
        "Python execution failed: RuntimeError: boom in simulate — fix the "
        "model or its settings, then queue it again."
    )
    assert strip.tip == CRASH


def test_a_failed_run_that_said_nothing_of_why_keeps_the_plain_sentence():
    plain = "The simulation failed. Fix the model or its settings, then queue it again."
    for failure in (None, "", "Simulation failed"):
        strip = strip_for(status="SIM_FAIL", has_result=True, failure=failure)
        assert (strip.sentence, strip.tip) == (plain, "")


def test_a_failed_partial_result_says_where_it_stopped_and_why():
    partial = Partial(live_rows.FAILED, 5.85, 30.0)
    strip = strip_for(
        status="SIM_FAIL", has_result=True, partial=partial, failure=CRASH
    )
    assert strip.sentence == (
        "Failed at t = 5.85 of 30 s — not scored. Python execution failed: "
        "RuntimeError: boom in simulate — fix the model or its settings, then "
        "queue it again."
    )
    assert strip.tip == CRASH
    # a cancel is nobody's failure: no reason to say
    stopped = Partial(live_rows.CANCELLED, 5.85, 30.0)
    cancelled = strip_for(status="NOT RUN", partial=stopped, failure="No simulation")
    assert cancelled.sentence == "Cancelled at t = 5.85 of 30 s — not scored"
    assert cancelled.tip == ""


def test_a_test_no_worker_could_start_says_one_line_of_the_pool_s_message():
    message = "No Dymola worker could start — worker 0: licence\nsecond line"
    row = replace(START_FAILED, message=message)
    strip = strip_for(queue=row, status="NOT RUN")
    assert _said(strip) == (
        S.START_FAILED,
        "red",
        "No Dymola worker could start — worker 0: licence",
    )
    assert strip.tip == message
    # a message that is not a failure's is not said as one
    passing = strip_for(status="PASS", has_result=True, failure="ignored")
    assert passing.sentence == "Passes against its baseline." and passing.tip == ""
