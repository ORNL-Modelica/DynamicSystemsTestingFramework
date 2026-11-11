"""A failed run's message as its result keeps it: what the test's error
says on every surface (the console, the report, the tool, ``dstf summary``).

A simulator's reason for stopping is usually its LAST line, after whatever
it repeated on the way: OpenModelica repeats a solver warning once per step
it retries. Kept head-first, a large library's message was 2,048 characters
of one warning and no reason. So a run of one repeated line is said once
with its count, and a message still too long keeps its beginning and its
end, with how much was left out between.
"""

from __future__ import annotations

#: The longest message a failed run keeps, in characters.
MESSAGE_LIMIT = 2048
#: How much of the beginning a message too long keeps; the rest of the
#: limit is its end, where the reason is.
HEAD = 500


def _collapsed(text: str) -> str:
    """``text`` with each run of one non-blank line said once:
    ``<line> (repeated N times)``, N the run's length."""
    out: list[str] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        n = 1
        while i + n < len(lines) and lines[i + n] == line:
            n += 1
        if n > 1 and line.strip():
            out.append(f"{line} (repeated {n} times)")
        else:
            out.extend([line] * n)
        i += n
    return "\n".join(out)


def _left_out(n: int) -> str:
    return f"\n… {n} characters left out …\n"


def failure_message(text: str) -> str:
    """``text`` as a failed run's result keeps it: repeats collapsed
    (:func:`_collapsed`); then, when still over :data:`MESSAGE_LIMIT`, its
    first :data:`HEAD` characters and as much of its end as the limit
    leaves, with a line between saying how many characters were left out.
    A message with no repeated line within the limit is returned as is."""
    text = _collapsed(text)
    if len(text) <= MESSAGE_LIMIT:
        return text
    # The marker counted with the whole length has the most digits it can,
    # so the one stating the true count is no longer.
    tail = MESSAGE_LIMIT - HEAD - len(_left_out(len(text)))
    return text[:HEAD] + _left_out(len(text) - HEAD - tail) + text[-tail:]
