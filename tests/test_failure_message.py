"""A failure message as a test's result keeps it
(``backends.common.failure_message``): a run of one repeated line is said
once with its count, and a message still too long keeps its beginning AND
its end — the reason a simulator gives is usually its last line."""

from dstf.backends.common.failure_message import failure_message

#: OpenModelica's warning, as a large library's run repeated it until the
#: 2,048 characters kept held nothing else.
WARNING = (
    "LOG_STDOUT        | warning | The default linear solver fails, the "
    "fallback solver with total pivoting is started at time 0.000000. That "
    "might raise performance issues, for more information use -lv LOG_LS."
)
HEADER = "Simulation execution failed for model: Lib.Examples.Facility"
#: The real run's reason was cut off with everything past 2,048 characters;
#: this line stands in for it.
REASON = "LOG_STDOUT        | error   | the reason the run stopped"


def test_a_repeated_warning_is_said_once_and_the_reason_after_it_kept():
    text = "\n".join([HEADER, *[WARNING] * 40, REASON])
    assert len(text) > 2048

    assert failure_message(text) == (
        f"{HEADER}\n{WARNING} (repeated 40 times)\n{REASON}"
    )


def test_a_short_message_is_unchanged_byte_for_byte():
    text = (
        "OMCSessionZMQ error: OMCSessionException: [OMC log for "
        "'sendExpression(simulate(Lib.A, stopTime=1.0), False)']: "
        "[translation:error:63] Internal error It is not possible to select "
        "continuous time states because Number of Equations 15 greater than "
        "number of States 0 to select from.\n\n  indented line\n"
    )
    assert failure_message(text) == text


def test_repeated_blank_lines_are_kept_as_they_are():
    text = "first\n\n\n\nlast"
    assert failure_message(text) == text


def test_too_long_with_no_repeat_keeps_the_beginning_and_the_end():
    lines = [f"line {i:04d} " + "x" * 40 for i in range(100)]
    text = "\n".join([*lines, REASON])
    assert len(text) == 5156

    got = failure_message(text)

    assert len(got) == 2048
    assert got.startswith(text[:500])
    assert got.endswith(text[-1518:])
    assert got.endswith(REASON)
    assert "\n… 3138 characters left out …\n" in got


def test_a_repeat_that_does_not_bring_it_under_the_limit_is_collapsed_first():
    distinct = [f"step {i:03d} " + "y" * 60 for i in range(60)]
    text = "\n".join([HEADER, *[WARNING] * 30, *distinct, REASON])

    got = failure_message(text)

    assert got.startswith(f"{HEADER}\n{WARNING} (repeated 30 times)\n")
    assert got.endswith(f"{distinct[-1]}\n{REASON}")
    assert len(got) <= 2048


def test_windows_line_ends_collapse_without_a_carriage_return_in_the_line():
    """A ``\\r`` left before the count makes a terminal overwrite the line."""
    text = "warn A\r\nwarn A\r\nwarn A\r\nthe reason\r\n"
    out = failure_message(text)
    assert "warn A (repeated 3 times)" in out
    assert "\r (repeated" not in out
    assert out.endswith("the reason\r\n")
