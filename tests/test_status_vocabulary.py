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
        # D150: a cancelled run left no result, and is counted as NOT RUN
        "CANCELLED": "not-run",
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
    # D150: the simulation queue's cancel — a word, never the raw state
    assert st.live_status("cancelled") == "CANCELLED"
    assert "cancelled" in st.LIVE_STATES


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
        "CANCELLED",
    )


def test_a_cancelled_run_is_counted_as_not_run():
    """D150: CANCELLED is its own word and no bucket of its own — it wears
    NOT RUN's class, so every pill, tint and filter counts it there, and
    the pills (one per class) do not list it."""
    assert st.CANCELLED == st.STATUS_LABELS[st.CANCELLED] == "CANCELLED"
    assert st.STATUS_CLASSES[st.CANCELLED] == st.STATUS_CLASSES[st.NOT_RUN]
    assert st.CANCELLED not in st.FAILURES
    assert st.PILL_STATUSES == tuple(s for s in st.STATUS_ORDER if s != st.CANCELLED)
    classes = [st.STATUS_CLASSES[s] for s in st.PILL_STATUSES]
    assert len(classes) == len(set(classes))


def test_the_statuses_that_fail_a_run():
    """D145: what the exit code counts. NOT RUN is not in it."""
    assert st.FAILURES == frozenset({"FAIL", "SIM_FAIL"})


def test_not_run_wears_the_neutral_grey_token():
    """D145: NOT RUN is drawn in the existing neutral grey (the queued
    token), under its own class so a pill and a filter can tell it apart."""
    from dstf.reporting import theme

    assert theme.STATUS["not-run"] == theme.STATUS["queued"]
