"""A test's situation and the strip's sentence (the Test page's status strip)."""

from __future__ import annotations

import pytest

from dstf.authoring.situation import Situation, Strip, strip_for

S = Situation

ROWS = [
    (
        dict(running="Rerun", status="FAIL"),
        S.RUNNING,
        "grey",
        "Rerun is running on this test.",
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
    (
        dict(status=None, has_result=False),
        S.NO_RESULT,
        "grey",
        "No result yet — rerun to simulate it.",
    ),
    # D145: the classifier's NOT RUN is this strip's "No result yet", and
    # only it is: the header's badge and the strip say one thing.
    (
        dict(status="NOT RUN", has_result=False),
        S.NO_RESULT,
        "grey",
        "No result yet — rerun to simulate it.",
    ),
    (
        dict(status="NOT RUN", has_result=True),
        S.NO_RESULT,
        "grey",
        "No result yet — rerun to simulate it.",
    ),
    (
        dict(status="SIM_FAIL", has_result=False),
        S.NO_RESULT,
        "grey",
        "No result yet — rerun to simulate it.",
    ),
    (
        dict(status="SIM_FAIL", has_result=True),
        S.SIM_FAIL,
        "red",
        "The simulation failed. Fix the model or its settings, then rerun.",
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
        (dict(running="Rerun", edits=2, status="FAIL"), S.RUNNING),
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
        " · 1 new variable: rerun to score it",
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
        "2 unsaved edits · verdict stays FAIL · 2 new variables: rerun to score them",
    ),
    # Saved without a rerun: the leaf still reads "not found in the result",
    # and "accept it" would store a baseline without the variable.
    (
        dict(status="FAIL", has_result=True, fails=1, criteria=2, new_variables=1),
        S.NEW_VARIABLES,
        "amber",
        "1 new variable the last run did not record: rerun to score it.",
    ),
    (
        dict(status="NO_REF", has_result=True, new_variables=2),
        S.NEW_VARIABLES,
        "amber",
        "2 new variables the last run did not record: rerun to score them.",
    ),
]


@pytest.mark.parametrize(("inputs", "situation", "tone", "sentence"), NEW_ROWS)
def test_new_variables_ask_for_a_rerun(inputs, situation, tone, sentence):
    assert strip_for(**inputs) == Strip(situation, tone, sentence)


def test_nothing_new_is_todays_sentence():
    for inputs, situation, tone, sentence in ROWS:
        assert strip_for(**inputs, new_variables=0) == Strip(situation, tone, sentence)


@pytest.mark.parametrize(
    ("inputs", "first"),
    [
        (dict(running="Rerun", edits=1, new_variables=1), S.RUNNING),
        (dict(status="SIM_FAIL", has_result=True, new_variables=1), S.SIM_FAIL),
        (dict(status=None, has_result=False, new_variables=1), S.NO_RESULT),
        (dict(status=None, has_result=True, new_variables=1), S.WAITING),
        (dict(scoring_failed=True, new_variables=1), S.SCORING_FAILED),
        (dict(status="PASS", has_result=True, new_variables=1), S.NEW_VARIABLES),
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
    criterion's variable counted as new, and the strip asked for a rerun to
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
    one a rerun would score: never NEW_VARIABLES, never EDITS_NEW."""
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
