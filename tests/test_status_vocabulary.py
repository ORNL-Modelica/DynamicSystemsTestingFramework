"""One status vocabulary: the verdicts, NOT RUN and the three live states.

Every surface that names a test's state — the dashboard's rows and pills,
the per-test report header, the tool's Suite table — reads its word and
its CSS class from ``reporting.status``, so two surfaces cannot call one
state by two names.
"""

from dstf.reporting import status as st


def test_every_status_has_one_word_and_one_class():
    assert st.STATUS_CLASSES == {
        "PASS": "pass",
        "FAIL": "fail",
        "SIM_FAIL": "sim-fail",
        "NO_REF": "no-ref",
        "QUEUED": "queued",
        "RUNNING": "running",
        "TIMED_OUT": "timed-out",
        "NOT RUN": "not-run",
    }


def test_the_word_a_person_reads():
    assert st.STATUS_LABELS["TIMED_OUT"] == "TIMED OUT"
    assert all(
        st.STATUS_LABELS[s] == s
        for s in ("PASS", "FAIL", "SIM_FAIL", "NO_REF", "QUEUED", "RUNNING")
    )
    # D145: one word, the key and the label alike, so a surface that shows
    # the classifier's key (the tool's badges and drawer pills) reads it too
    assert st.NOT_RUN == st.STATUS_LABELS[st.NOT_RUN] == "NOT RUN"


def test_a_live_state_before_any_comparison():
    # status.json's words; "passed"/"failed" there mean the SIMULATION ran
    # or did not, not a verdict, so they are not PASS/FAIL
    assert st.live_status("queued") == "QUEUED"
    assert st.live_status("running") == "RUNNING"
    assert st.live_status("timed_out") == "TIMED_OUT"
    assert st.live_status("failed") == "SIM_FAIL"
    assert st.live_status("passed") is None  # simulated, not yet scored


def test_the_order_pills_and_sorting_use():
    assert st.STATUS_ORDER == (
        "QUEUED",
        "RUNNING",
        "PASS",
        "FAIL",
        "SIM_FAIL",
        "NO_REF",
        "TIMED_OUT",
        "NOT RUN",
    )


def test_the_statuses_that_fail_a_run():
    """D145: what the exit code counts. NOT RUN is not in it."""
    assert st.FAILURES == frozenset({"FAIL", "SIM_FAIL"})


def test_not_run_wears_the_neutral_grey_token():
    """D145: NOT RUN is drawn in the existing neutral grey (the queued
    token), under its own class so a pill and a filter can tell it apart."""
    from dstf.reporting import theme

    assert theme.STATUS["not-run"] == theme.STATUS["queued"]
