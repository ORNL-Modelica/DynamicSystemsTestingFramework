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
    (
        dict(status=None, has_result=False),
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
