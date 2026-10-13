"""The filter both drawers of the Test page ask (Tests and Variables): a
needle as a substring, a glob or a typed regex, the regex held to a budget
of its own CPU and of the UI loop's wall time."""

from __future__ import annotations

import re
from collections.abc import Callable
from time import monotonic, thread_time

from ..backends.types import is_glob_pattern

#: Seconds of the searching thread's CPU (`thread_time`) ONE typed regex
#: may search, over every name of one filter: the verdict "the pattern took
#: too long" is the filter's own CPU, so waits for the GIL never make a
#: plain pattern too long. An ordinary search costs ~2.5 us a name and
#: reading that clock ~0.85 us more (a system call here; `monotonic` is
#: ~0.075 us): on an idle machine TRANSFORM's largest result (76,992 names)
#: takes ~0.27 s, and 0.25 s timed out a plain `T` over 100,000. 1 s leaves
#: ~3.5x there; a loaded WSL2 host measured up to 0.99 s of thread CPU for
#: the same filter, its `thread_time` counting time the host took the vCPU
#: away. The clock is the thread's own because between searches the filter
#: waits for the GIL on any busy thread of the tool (its overlay index
#: json.loads for ~15 s after a page opens): in wall time those names took
#: 0.4-0.9 s beside one or two such threads, two to three times their own
#: CPU. Each search holds the GIL (``concurrent=False``): released, every
#: search waited on a busy thread again, 0.3-0.9 ms a name beside one, and
#: `T` on a wall-clock budget ran out after 1,200-3,600 names.
#: The package times each search's ``timeout`` with C ``clock()``: on
#: Linux and macOS the CPU time of the whole process, every thread summed;
#: on Windows, wall time.
REGEX_BUDGET = 1.0
#: Seconds of WALL time (`monotonic`) the same filter may take. It runs on
#: the UI event loop, so every tab is frozen for its wall time, which the
#: CPU budget does not bound: beside a thread holding the GIL ~0.2 s at a
#: time, a backtracking pattern froze the tool 17.4 s on 1 s of its own
#: CPU, and a plain `T` over TRANSFORM's largest result took 10.7-20.5 s
#: and never ran out. This is what bounds the freeze every tab sees. It is
#: checked between searches: a search is given the smaller time left, but
#: `regex` counts it on ``clock()`` — the process's CPU on Linux and macOS —
#: so one search is bounded by the CPU left and can pass the wall time left.
REGEX_WALL_BACKSTOP = 5 * REGEX_BUDGET


def matcher(needle: str, *, regex: bool, case: bool) -> Callable[[str], bool]:
    """What the filter box keeps. A regex (``.*`` on) searches; a glob (a
    ``*`` or ``?`` in it) matches the WHOLE text with ``[ ]`` literal — the
    rule a spec's ``variables`` list uses, so what you type here means what
    it means there (`_glob`); anything else is a substring. ``regex.error``
    for a bad regex, and ``TimeoutError`` from the returned closure once the
    regex has searched `REGEX_BUDGET` seconds of its thread's CPU in all,
    for the drawer to say.

    A typed regex is the `regex` package's, never `re`'s: `re` has no
    timeout, so a pattern that backtracks runs to its end (`^(a|aa)+$` over
    60 a's and a ``b``: days), and `regex` stops a search at its
    ``timeout``. Both hold the GIL for a whole search, `regex` because
    ``concurrent=False`` asks it to, and the filter runs on the UI event
    loop, so what bounds the freeze every tab sees is
    `REGEX_WALL_BACKSTOP`'s wall time, not a worker thread; `REGEX_BUDGET`,
    the filter's own CPU, decides a pattern that took too long. It is imported here, not at the
    top: it comes with the ``[author]`` extra, and this module imports
    without it. A glob is not left to a regex either — compiled to one,
    ``*a*a*a*a*a*a*a*b`` over one 60-character name took over 20 s on the
    UI event loop."""
    flags = 0 if case else re.IGNORECASE
    if not needle:
        return lambda text: True
    if regex:
        import regex as regex_package

        # unbounded: `(a{5000}){5000}` compiles for seconds, or out of memory
        pattern = regex_package.compile(needle, 0 if case else regex_package.IGNORECASE)
        # CPU, then wall; set at the first name, not at compile. The closure
        # is one thread's: `thread_time` readings from two threads cannot be
        # compared, and each drawer builds and uses it within one pass
        # (`VariablesDrawer._build`, `TestsDrawer._kept`).
        deadlines: list[float] = []

        def search(text: str) -> bool:
            cpu, wall = thread_time(), monotonic()
            if not deadlines:
                deadlines.extend((cpu + REGEX_BUDGET, wall + REGEX_WALL_BACKSTOP))
            left = min(deadlines[0] - cpu, deadlines[1] - wall)
            if left <= 0:
                raise TimeoutError("the pattern took too long")
            return pattern.search(text, timeout=left, concurrent=False) is not None

        return search
    if is_glob_pattern(needle):
        return _glob(needle, flags)
    folded = needle if case else needle.lower()
    return lambda text: folded in (text if case else text.lower())


def _glob(needle: str, flags: int) -> Callable[[str], bool]:
    """``needle`` matched against the whole text as `_pattern_to_regex`'s
    anchored regex matches it, without backtracking. Split on ``*``, each
    piece is a fixed-length regex (``?`` is ``.``, everything else literal)
    with no quantifier: the first must match at the start, the last at the
    end, and each middle one is found left to right — the leftmost place
    leaves the most text for the rest, so no other place is ever tried. A
    ``*`` spans no newline and ``?`` matches none, as the regex's ``.``
    does not (`test_the_glob_means_what_the_specs_regex_means`)."""
    pieces = [
        re.compile("".join("." if ch == "?" else re.escape(ch) for ch in p), flags)
        for p in needle.split("*")
    ]
    if len(pieces) == 1:  # a ``?`` and no ``*``
        (only,) = pieces
        return lambda text: only.fullmatch(text) is not None
    head, *middle, tail = pieces
    width = len(needle.rsplit("*", 1)[1])  # the last piece's, one per character

    def keep(text: str) -> bool:
        start, end = head.match(text), len(text) - width
        if start is None or end < start.end() or tail.fullmatch(text, end) is None:
            return False
        at = start.end()
        for piece in middle:
            found = piece.search(text, at, end)
            if found is None or text.find("\n", at, found.start()) != -1:
                return False
            at = found.end()
        return text.find("\n", at, end) == -1

    return keep
