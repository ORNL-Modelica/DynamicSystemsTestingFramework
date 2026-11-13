"""``dstf uses``: which tests use a class, asked of the project's simulator.

stdout is the answer — ``dstf uses X > file`` holds nothing else; what the
command is doing, and why it could not answer, goes to stderr.
"""

import argparse
import sys
from pathlib import Path

from ..config import Config, ConfigError
from ..discovery.test_registry import discover_tests
from .common import _build_config, _filter_tests


def _answering(cls) -> bool:
    """Whether the backend ``cls`` can say what a test uses: read on the
    class a run without ``--batch`` uses, which is where a backend declares
    what its live simulator can do (``dstf uses`` has no ``--batch``)."""
    from ..backends.types import Capability

    return Capability.USES in (cls.persistent_backend_cls() or cls).capabilities


def _hint(config: Config) -> str | None:
    """The backends here that can answer for this project, as one sentence
    naming the ``--backend`` to give; None when there is none. A candidate
    is an entry of the project's ``backends`` map, by its block, or a
    registered backend it has no entry of (``dstf check``'s list); it is
    named when it reads the project's source type, can answer, and is found
    on this machine — what each backend declares, never its name."""
    from ..backends import backend_class, backend_family
    from .diag_cmds import _check_entries

    can = []
    for name, block in _check_entries(config):
        cls = backend_class(backend_family(name))
        if cls is None or config.source_type not in cls.source_types:
            continue
        if _answering(cls) and cls.locate(block).found:
            can.append(name)
    if not can:
        return None
    first = f'"{can[0]}"' if " " in can[0] else can[0]
    verb = "is" if len(can) == 1 else "are"
    return f"{' and '.join(can)} {verb} installed here and can: add --backend {first}."


def _believed(
    said: dict[str, frozenset[str] | str], model_id: str, label: str
) -> frozenset[str] | str:
    """What the backend said of one test, or why it is not believed — and
    the test then counts as using every class, as one it could not ask
    does: it left the test out, or its answer does not hold the test's own
    model, which every answer holds (:meth:`Backend.uses`). Such an answer
    would read as a test that uses nothing. A reason is made one line here,
    whichever backend gave it: it is printed on its test's line."""
    from ..discovery.uses import one_line

    if model_id not in said:
        return f"{label} gave no answer for it"
    got = said[model_id]
    if isinstance(got, str):
        return one_line(got, f"{label} could not answer for it and said nothing")
    if model_id not in got:
        return f"{label}'s answer does not hold the test's own model"
    return got


def cmd_uses(args: argparse.Namespace) -> int:
    """List, for each class named, the tests whose model uses it."""
    from ..backends import BackendUnavailableError, get_backend_class, select_backend
    from ..discovery.uses import is_class, users

    config = _build_config(args)
    try:
        cls = get_backend_class(config)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    if not _answering(cls):
        print(f"{config.backend_name} cannot say what a test uses.", file=sys.stderr)
        hint = _hint(config)
        if hint:
            print(hint, file=sys.stderr)
        return 1
    if config.source_type == "modelica":
        for name in args.classes:
            if not is_class(config, name):
                print(
                    f"Error: {name} is no class of the library "
                    f"{config.library_name} or of a dependency given as a path.",
                    file=sys.stderr,
                )
                return 2
    tests = _filter_tests(discover_tests(config), args.filter, args.package)
    if not tests:
        print("No tests found.", file=sys.stderr)
        return 1
    try:
        backend = select_backend(config, persistent=True, to_run=True)
    except BackendUnavailableError as exc:
        print(exc, file=sys.stderr)
        return 1

    asked = "1 test uses" if len(tests) == 1 else f"{len(tests)} tests use"
    print(f"Asking {backend.label} what {asked} ...", file=sys.stderr)
    try:
        said = backend.uses(tests)
    except Exception as exc:
        # The simulator did not start, or stopped answering: no test was
        # answered for, and none is counted.
        print(f"{backend.label} could not be asked: {exc}", file=sys.stderr)
        return 1
    answers = {t.model_id: _believed(said, t.model_id, backend.label) for t in tests}

    total = len(tests)
    union: set[str] = set()
    unasked: list[str] = []
    for name in args.classes:
        using, unasked = users(answers, name)
        union.update(using, unasked)
        n = len(using) + len(unasked)
        if n == 0:
            none = (
                "is not used by the 1 test"
                if total == 1
                else (f"is used by none of the {total} tests")
            )
            print(f"{name} {none}.")
            continue
        print(f"{name} is used by {_among(n, total)}:")
        for test in using:
            print(f"  {test}")
    if unasked:
        each = "it" if len(args.classes) == 1 else "each"
        print(f"Could not be asked (counted as using {each}):")
        for test in unasked:
            print(f"  {test} — {answers[test]}")
    if len(args.classes) > 1:
        k = len(union)
        if k == 0:
            print("No test uses any of them.")
        else:
            among = _among(k, total)
            verb = "uses" if k == 1 else "use"
            print(f"{among[0].upper()}{among[1:]} {verb} at least one of them.")
    if args.output is not None:
        Path(args.output).write_text(
            "".join(f"{test}\n" for test in sorted(union)), encoding="utf-8"
        )
        print(f"Wrote {args.output}: {len(union)} test{'' if len(union) == 1 else 's'}")
    return 0


def _among(n: int, total: int) -> str:
    """``n`` of the ``total`` tests asked, ``n`` at least 1, in words:
    ``3 of 14 tests``, or ``the 1 test`` when one was asked."""
    return "the 1 test" if total == 1 else f"{n} of {total} tests"
