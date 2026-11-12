"""`--shard I/N`: run every N-th selected test, starting at the I-th.

CI's browser job is split over three runners, each with `--shard I/3`. The
cut is by test, dealt round-robin in collection order, not by file: the slow
tests sit together in a few files, and a list of files per runner would have
to be kept in step with every new test file.

`tests/conftest.py` imports both hooks; `tests/test_shard.py` loads this
module on its own with `-p _shard`.
"""

import argparse

import pytest


def _shard(value: str) -> tuple[int, int]:
    """`I/N` with 1 <= I <= N, as (I, N); anything else is refused by argparse."""
    index, _, count = value.partition("/")
    try:
        i, n = int(index), int(count)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected I/N, such as 1/3, got {value!r}"
        ) from None
    if not 1 <= i <= n:
        raise argparse.ArgumentTypeError(
            f"expected I/N with 1 <= I <= N, got {value!r}"
        )
    return i, n


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--shard",
        type=_shard,
        default=None,
        metavar="I/N",
        help="run only the I-th of N shards of the selected tests (dealt round-robin; I counts from 1)",
    )


# trylast: after `-m` and `-k` have deselected theirs (the mark plugin's hook
# is unordered, and a conftest's would otherwise run first), so each shard is a
# share of the tests this run selects. Every xdist worker collects the same
# items in the same order and so keeps the same ones.
@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    shard = config.getoption("shard")
    if shard is None:
        return
    i, n = shard
    kept = [item for k, item in enumerate(items) if k % n == i - 1]
    deselected = [item for k, item in enumerate(items) if k % n != i - 1]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = kept
