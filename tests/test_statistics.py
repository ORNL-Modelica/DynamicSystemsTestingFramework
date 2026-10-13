"""The zero-count rule: a system-size list's summaries, written always and
read as 0 where a baseline written before that holds the list empty."""

from __future__ import annotations

from dstf.backends.common.statistics import read_summary, summaries


def test_summaries_of_an_empty_list_are_zeros():
    assert summaries("nonlinear", []) == {
        "nonlinear_count": 0,
        "nonlinear_total": 0,
        "nonlinear_max": 0,
    }


def test_summaries_of_a_list():
    assert summaries("nonlinear", [3, 5]) == {
        "nonlinear_count": 2,
        "nonlinear_total": 8,
        "nonlinear_max": 5,
    }


def test_an_empty_list_reads_its_summaries_as_zero():
    for field in ("count", "total", "max"):
        assert read_summary({"nonlinear": []}, f"nonlinear_{field}") == 0


def test_no_list_is_not_guessed():
    assert read_summary({}, "nonlinear_count") is None


def test_a_non_empty_list_without_its_count_is_not_guessed():
    assert read_summary({"nonlinear": [2]}, "nonlinear_count") is None


def test_a_key_that_is_not_a_summary_is_missing():
    assert read_summary({"nonlinear": []}, "nonlinear") is None


def test_an_after_manipulation_count_reads_its_own_list():
    cat = {"nonlinear_after_manipulation": []}
    assert read_summary(cat, "nonlinear_after_manipulation_count") == 0
    assert read_summary({"nonlinear": []}, "nonlinear_after_manipulation_count") is None
