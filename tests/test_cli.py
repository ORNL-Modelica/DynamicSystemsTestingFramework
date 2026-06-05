"""Tests for CLI utilities (review filtering, argument parsing)."""

import argparse
from dataclasses import dataclass, field

import pytest

from dstf.cli import (
    _parse_review_filter,
    _should_review,
    _VALID_REVIEW_FILTERS,
)


@dataclass
class FakeComparison:
    """Minimal stand-in for TestComparison for filter testing."""

    model_id: str = "Test.Model"
    passed: bool = True
    sim_success: bool = True
    has_reference: bool = True
    warnings: list = field(default_factory=list)


# --- _parse_review_filter ---


def test_parse_filter_all():
    assert _parse_review_filter("all") == {"all"}


def test_parse_filter_single():
    assert _parse_review_filter("failed") == {"failed"}


def test_parse_filter_multiple():
    result = _parse_review_filter("failed,no-baseline,warnings")
    assert result == {"failed", "no-baseline", "warnings"}


def test_parse_filter_all_absorbs_others():
    assert _parse_review_filter("all,failed") == {"all"}


def test_parse_filter_strips_whitespace():
    result = _parse_review_filter(" failed , no-baseline ")
    assert result == {"failed", "no-baseline"}


def test_parse_filter_invalid_raises():
    with pytest.raises(argparse.ArgumentTypeError, match="Invalid review filter"):
        _parse_review_filter("bogus")


def test_parse_filter_partial_invalid_raises():
    with pytest.raises(argparse.ArgumentTypeError, match="bogus"):
        _parse_review_filter("failed,bogus")


# --- _should_review ---


def test_should_review_all_matches_everything():
    comp = FakeComparison()
    assert _should_review(comp, {"all"}) is True


def test_should_review_failed():
    comp = FakeComparison(passed=False)
    assert _should_review(comp, {"failed"}) is True


def test_should_review_failed_skips_passing():
    comp = FakeComparison(passed=True)
    assert _should_review(comp, {"failed"}) is False


def test_should_review_failed_skips_no_baseline():
    """A test with no baseline is 'no-baseline', not 'failed'."""
    comp = FakeComparison(passed=False, has_reference=False)
    assert _should_review(comp, {"failed"}) is False


def test_should_review_failed_skips_sim_failed():
    comp = FakeComparison(passed=False, sim_success=False)
    assert _should_review(comp, {"failed"}) is False


def test_should_review_no_baseline():
    comp = FakeComparison(has_reference=False)
    assert _should_review(comp, {"no-baseline"}) is True


def test_should_review_no_baseline_skips_existing():
    comp = FakeComparison(has_reference=True)
    assert _should_review(comp, {"no-baseline"}) is False


def test_should_review_warnings():
    comp = FakeComparison(warnings=["something changed"])
    assert _should_review(comp, {"warnings"}) is True


def test_should_review_warnings_skips_clean():
    comp = FakeComparison(warnings=[])
    assert _should_review(comp, {"warnings"}) is False


def test_should_review_sim_failed():
    comp = FakeComparison(sim_success=False)
    assert _should_review(comp, {"sim-failed"}) is True


def test_should_review_sim_failed_skips_success():
    comp = FakeComparison(sim_success=True)
    assert _should_review(comp, {"sim-failed"}) is False


def test_should_review_passed():
    comp = FakeComparison(passed=True, warnings=[])
    assert _should_review(comp, {"passed"}) is True


def test_should_review_passed_skips_with_warnings():
    comp = FakeComparison(passed=True, warnings=["warn"])
    assert _should_review(comp, {"passed"}) is False


def test_should_review_passed_skips_failed():
    comp = FakeComparison(passed=False)
    assert _should_review(comp, {"passed"}) is False


def test_should_review_combined_filters():
    """Combined filters match if any filter matches."""
    filters = {"failed", "no-baseline"}
    assert _should_review(FakeComparison(passed=False), filters) is True
    assert _should_review(FakeComparison(has_reference=False), filters) is True
    assert _should_review(FakeComparison(passed=True), filters) is False


def test_should_review_sim_failed_with_failed():
    """sim-failed + failed covers both crash and comparison failure."""
    filters = {"sim-failed", "failed"}
    assert _should_review(FakeComparison(sim_success=False), filters) is True
    assert _should_review(FakeComparison(passed=False), filters) is True
    assert _should_review(FakeComparison(passed=True), filters) is False


# ---------------------------------------------------------------------------
# Parser <-> dispatch-table parity
# ---------------------------------------------------------------------------


def test_argparse_subcommands_match_dispatch_table():
    """Every subcommand registered with argparse must have a handler in
    ``_COMMANDS``, and vice versa. Catches drift when adding a new
    subcommand to one site but forgetting the other."""
    from dstf.cli import build_arg_parser, _COMMANDS

    parser = build_arg_parser()
    # subparsers action holds the subcommand registry
    subparsers_action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    parser_commands = set(subparsers_action.choices.keys())
    dispatch_commands = set(_COMMANDS.keys())
    assert parser_commands == dispatch_commands, (
        f"argparse subcommands and _COMMANDS dispatch table drifted:\n"
        f"  in argparse but not _COMMANDS: {parser_commands - dispatch_commands}\n"
        f"  in _COMMANDS but not argparse: {dispatch_commands - parser_commands}"
    )
