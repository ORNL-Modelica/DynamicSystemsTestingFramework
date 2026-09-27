"""The filter the drawers ask (`authoring.needle`): substring, glob and
typed regex, the regex held to its CPU budget and the wall backstop."""

from __future__ import annotations

import itertools
import re
import time

import pytest

from dstf.authoring import needle as needle_module
from dstf.authoring.needle import matcher


@pytest.mark.parametrize(
    ("needle", "regex", "case", "text", "hit"),
    [
        ("pipe", False, False, "PIPE.t[1]", True),  # substring, any case
        ("pipe", False, True, "PIPE.t[1]", False),
        ("pipe.T[*]", False, False, "pipe.T[12]", True),  # brackets literal
        ("pipe.T*", False, False, "a.pipe.T[1]", False),  # a glob is anchored
        ("", False, False, "anything", True),
    ],
)
def test_matcher(needle, regex, case, text, hit):
    assert matcher(needle, regex=regex, case=case)(text) is hit


def _regex_glob(needle: str, case: bool):
    """The glob as a spec's ``variables`` list reads it (`_pattern_to_regex`,
    anchored): what the drawer's glob must mean, exactly."""
    from dstf.backends.types import _pattern_to_regex

    glob = re.compile(_pattern_to_regex(needle).pattern, 0 if case else re.IGNORECASE)
    return lambda text: glob.fullmatch(text) is not None


def test_the_glob_means_what_the_specs_regex_means():
    """Every glob of up to four characters over a small alphabet against
    every text of up to four: the drawer's glob, which cannot backtrack,
    keeps exactly what the spec's anchored regex keeps — a case toggle, a
    ``.`` literal, a ``?`` that is one character but never a newline and a
    ``*`` that never spans one, as the regex's ``.`` does not."""
    needles = [
        "".join(p)
        for n in range(1, 5)
        for p in itertools.product("aA.?*\n", repeat=n)
        if "*" in p or "?" in p
    ]
    texts = [
        "".join(p) for n in range(5) for p in itertools.product("aAb.\n", repeat=n)
    ]
    for case in (False, True):
        for needle in needles:
            ours = matcher(needle, regex=False, case=case)
            theirs = _regex_glob(needle, case)
            for text in texts:
                assert ours(text) is theirs(text), (needle, text, case)


def test_a_glob_cannot_backtrack():
    """``*a*a*a*a*a*a*a*b`` over one 60-character name took over 20 s as a
    regex, on the UI event loop, blocking every tab."""
    keep = matcher("*a*a*a*a*a*a*a*b", regex=False, case=False)
    start = time.perf_counter()
    assert not keep("a" * 60)
    assert time.perf_counter() - start < 0.5


def test_a_bad_regex_raises_for_the_drawer_to_say_so():
    regex = pytest.importorskip("regex")
    with pytest.raises(regex.error):
        matcher("(", regex=True, case=False)


def test_a_regex_searches_with_case_on_and_off():
    pytest.importorskip("regex")
    name = "port[2, 1].C[1]"
    assert matcher("^port", regex=True, case=False)(name)
    assert matcher("^PORT", regex=True, case=False)(name)
    assert not matcher("^PORT", regex=True, case=True)(name)
    assert not matcher("^port", regex=True, case=True)("a.port")
    assert matcher(r"C\[1\]$", regex=True, case=True)(name)


_BACKTRACK = """
import sys, time
import dstf
from dstf.authoring.needle import matcher
assert dstf.__file__ == sys.argv[1], (dstf.__file__, sys.argv[1])
keep = matcher(r"^(a|aa)+$", regex=True, case=False)
start = time.process_time()
try:
    for _ in range(3):
        keep("a" * 60 + "b")
except TimeoutError:
    pass
print(time.process_time() - start)
"""


def test_a_regex_that_backtracks_finishes_within_the_budget():
    """`^(a|aa)+$` over a name that almost matches backtracks exponentially:
    the filter FINISHES near its budget — timed out, or answered if a later
    `regex` optimises the pattern away (it already does `(a+)+$`) — instead
    of freezing the tool. In a child process, so a filter that no longer
    stops fails on the timeout: without one this search was killed at 736 s
    and would have run for days. The timeout is generous because the
    child's import alone takes ~2 s here. The child times the filter in
    process-CPU seconds: the budget is the searching thread's CPU, and
    `regex` times a search's timeout with C `clock()` — on Linux and macOS
    the CPU of the whole process, every thread summed, which in a child of
    one thread is the same seconds; on Windows, wall time. On 2 loaded
    cores its wall time passed 1.5 budgets in 7 runs of 20."""
    pytest.importorskip("regex")
    import subprocess
    import sys

    import dstf

    done = subprocess.run(
        [sys.executable, "-c", _BACKTRACK, dstf.__file__],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    # the floor: a child timed on two clocks printed a negative number
    assert 0 <= float(done.stdout) < 1.5 * needle_module.REGEX_BUDGET


def test_a_plain_regex_over_a_big_result_does_not_run_out_of_time():
    """A search costs ~2.5 us a name: at a 0.25 s budget, 100,000 names
    timed out `T` itself, and TRANSFORM's largest result has 76,992."""
    pytest.importorskip("regex")
    names = [f"pipe{i}.T[{i % 20}]" for i in range(100_000)]
    keep = matcher(r"pipe\d+\.T\[3\]$", regex=True, case=False)
    kept = [n for n in names if keep(n)]
    assert len(kept) == 5_000 and kept[0] == "pipe3.T[3]"


def test_a_regex_is_timed_in_its_own_cpu_while_another_thread_holds_the_gil(
    monkeypatch,
):
    """The budget counts the searching thread's CPU, not the time it waits
    for the GIL between searches: beside a thread that holds the GIL ~0.2 s
    at a time (the overlay index json.loads-ing TRANSFORM's refs for ~15 s
    after a page opens), this filter over 10,000 names took 1.3-1.6 s of
    wall time and 0.044-0.050 s of its own CPU here in 22 runs, idle and
    beside two CPU burners on 8 cores; a loaded host saw up to 8.4 s and
    0.17 s. Without the busy thread it takes ~0.04 s, so a wall time over
    3 budgets proves the thread contended. A 0.3 s budget is 6x above that
    CPU and 4x below the fastest wall time: timed on `monotonic` it ran
    out, timed on `thread_time` it keeps every name. And each search holds
    the GIL (``concurrent=False``): released, a search waits on the busy
    thread every time, up to ~0.2 s a name, and failed here in 3 runs of 3
    at ~30 s: twice on the 30 s bound, once on `regex`'s own timeout, which
    counts the busy thread's CPU."""
    pytest.importorskip("regex")
    import threading

    stop = threading.Event()

    def spin() -> None:
        while not stop.is_set():
            sum(range(20_000_000))  # one call holds the GIL ~0.2 s

    monkeypatch.setattr(needle_module, "REGEX_BUDGET", 0.3)
    # the wall backstop would end the slow runs this test measures the CPU of
    monkeypatch.setattr(needle_module, "REGEX_WALL_BACKSTOP", 60.0)
    names = [f"pipe{i}.T[{i % 20}]" for i in range(10_000)]
    keep = matcher(r"pipe\d+\.T\[3\]$", regex=True, case=False)
    kept = []
    busy = threading.Thread(target=spin, daemon=True)
    busy.start()
    try:
        start = time.perf_counter()
        for name in names:
            if keep(name):
                kept.append(name)
            assert time.perf_counter() - start < 30, f"waiting after {len(kept)} kept"
        wall = time.perf_counter() - start
    finally:
        stop.set()
        busy.join()
    assert wall > 3 * needle_module.REGEX_BUDGET, (
        f"{wall:.2f} s: the spinner never contended"
    )
    assert len(kept) == 500 and kept[0] == "pipe3.T[3]"


def test_the_budget_is_one_for_every_name_of_a_filter(monkeypatch):
    """ONE budget from the filter's first name, not one per name: 1 s a
    name over 6,093 names is 100 minutes. The clock is faked to read 0.4
    budgets later at each name while every search is instant, so a budget
    per name would never run out; the one total budget runs out at the 4th
    name, which a fresh filter keeps."""
    pytest.importorskip("regex")
    b = needle_module.REGEX_BUDGET
    ticks = iter([0.0, 0.4 * b, 0.8 * b, 1.2 * b, 1.2 * b])
    monkeypatch.setattr(needle_module, "thread_time", lambda: next(ticks))
    keep = matcher("x", regex=True, case=False)
    assert [keep("x") for _ in range(3)] == [True, True, True]
    with pytest.raises(TimeoutError):
        keep("x")
    assert matcher("x", regex=True, case=False)("x")


def test_a_search_is_given_the_time_left_not_the_whole_budget(monkeypatch):
    """The first name took 96% of the budget: the second search stops at
    the 4% left, well before a whole budget would. 32 a's, in this process:
    given no timeout the search runs 0.83-0.90 s here (case-insensitive,
    as below), 4x the bound, and ends — 60 a's would run for days."""
    pytest.importorskip("regex")
    ticks = iter([0.0, 0.96 * needle_module.REGEX_BUDGET])
    monkeypatch.setattr(needle_module, "thread_time", lambda: next(ticks))
    keep = matcher(r"^(a|aa)+$", regex=True, case=False)
    assert keep("aa")
    start = time.perf_counter()
    with pytest.raises(TimeoutError):
        keep("a" * 32 + "b")
    assert time.perf_counter() - start < 0.2


def test_the_wall_backstop_stops_a_filter_its_own_cpu_would_not(monkeypatch):
    """The filter runs on the UI event loop, so every tab is frozen for its
    WALL time: beside threads holding the GIL a plain `T` took 10.7-20.5 s
    with 1 s of its own CPU unspent. The wall clock is faked to read 0.4
    backstops later at each name while the thread's CPU stays real, and
    near nothing: the backstop runs out at the 4th name."""
    pytest.importorskip("regex")
    w = needle_module.REGEX_WALL_BACKSTOP
    ticks = iter([0.0, 0.4 * w, 0.8 * w, 1.2 * w])
    monkeypatch.setattr(needle_module, "monotonic", lambda: next(ticks))
    keep = matcher("x", regex=True, case=False)
    assert [keep("x") for _ in range(3)] == [True, True, True]
    with pytest.raises(TimeoutError):
        keep("x")


def test_a_search_is_given_the_wall_time_left_when_that_is_less(monkeypatch):
    """The first name took 99% of the backstop's wall time and none of the
    CPU budget: the second search stops at the wall time left, not at the
    whole CPU budget (32 a's run 0.83-0.90 s given no timeout, above)."""
    pytest.importorskip("regex")
    ticks = iter([0.0, 0.99 * needle_module.REGEX_WALL_BACKSTOP])
    monkeypatch.setattr(needle_module, "monotonic", lambda: next(ticks))
    keep = matcher(r"^(a|aa)+$", regex=True, case=False)
    assert keep("aa")
    start = time.perf_counter()
    with pytest.raises(TimeoutError):
        keep("a" * 32 + "b")
    assert time.perf_counter() - start < 0.2
