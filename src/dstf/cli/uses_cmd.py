"""``dstf uses``: which tests use a class, asked of the project's simulator
— the classes named, and with ``--since`` those changed since a git
revision (:func:`dstf.discovery.uses.changed_since`); this module only
prints.

stdout is the answer — ``dstf uses X > file`` holds nothing else; what the
command is doing, and why it could not answer, goes to stderr.
"""

import argparse
import sys
from collections.abc import Iterable
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
    """List, for each class named or changed since ``--since``, the tests
    whose model uses it."""
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
    if args.since is not None and config.source_type != "modelica":
        print(
            "--since reads a Modelica library's files; this project's "
            f"source_type is {config.source_type}.",
            file=sys.stderr,
        )
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
    changes = None
    if args.since is not None:
        from ..discovery.uses import changed_since
        from ..storage.git_revisions import GitError

        try:
            changes = changed_since(
                config, args.since, lambda line: print(line, file=sys.stderr)
            )
        except GitError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
    tests = _filter_tests(discover_tests(config), args.filter, args.package)
    if not tests:
        print("No tests found.", file=sys.stderr)
        return 1

    # Named classes first, as given; then the changed ones, by name.
    classes = list(dict.fromkeys(args.classes))
    block: list[str] = []
    if changes is not None:
        block = _changed_block(changes, args.since)
        if changes.every_test:
            print("\n".join(block))
            print(_every_test(changes, len(tests), len(classes)))
            _write(args.output, [t.model_id for t in tests])
            return 0
        if not changes.classes and not classes:
            print("\n".join(block))
            _write(args.output, [])
            return 0
        classes = list(dict.fromkeys([*classes, *changes.classes]))
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

    # The changes are printed with the answer, never without it: stdout
    # holds nothing when the simulator could not be asked.
    for line in block:
        print(line)
    total = len(tests)
    union: set[str] = set()
    unasked: list[str] = []
    for name in classes:
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
        each = "it" if len(classes) == 1 else "each"
        print(f"Could not be asked (counted as using {each}):")
        for test in unasked:
            print(f"  {test} — {answers[test]}")
    if len(classes) > 1:
        k = len(union)
        if k == 0:
            print("No test uses any of them.")
        else:
            among = _among(k, total)
            verb = "uses" if k == 1 else "use"
            print(f"{among[0].upper()}{among[1:]} {verb} at least one of them.")
    _write(args.output, union)
    return 0


def _write(output: str | None, tests: Iterable[str]) -> None:
    """``--output``: the tests one per line, sorted — an empty file for
    none — and the line that says so."""
    if output is None:
        return
    ids = sorted(tests)
    Path(output).write_text("".join(f"{test}\n" for test in ids), encoding="utf-8")
    print(f"Wrote {output}: {_count(len(ids), 'test')}")


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


#: The longest subject of a commit that is printed, in characters.
SUBJECT_LIMIT = 60

#: The most files listed per repository; the rest are counted.
LISTED = 20


def _changed_block(changes, rev: str) -> list[str]:
    """What differs since ``rev``, as printed before the answer: per
    repository, a line naming the revision as it resolved there, then the
    files (each with the class asked, the package a deleted one is asked
    as, or why it counts every test), under their root's name when the
    project has several roots; then the roots in no repository."""
    several_repos = len(changes.repos) > 1
    several_roots = (
        sum(len(repo.roots) for repo in changes.repos) + len(changes.unversioned) > 1
    )
    lines: list[str] = []
    for repo in changes.repos:
        subject = repo.commit.subject
        if len(subject) > SUBJECT_LIMIT:
            subject = subject[: SUBJECT_LIMIT - 1].rstrip() + "…"
        at = f"{rev} ({repo.commit.short} {subject})"
        files = repo.files
        if files:
            lines.append(f"Changed since {at}: {_count(len(files), 'file')}")
        else:
            where = ", ".join(r.root.label for r in repo.roots)
            lines.append(
                f"Nothing changed since {at}{f' in {where}' if several_repos else ''}."
            )
        rows: list[tuple[str | None, str, str]] = []
        for root in repo.roots:
            heading = root.root.label if several_roots else None
            rows += [(heading, f.path, _what(f)) for f in root.files]
            rows += [
                (heading, f"{p}/", "another repository: not compared")
                for p in root.not_compared
            ]
        shown, rest = rows[:LISTED], len(rows) - LISTED
        width = max((len(path) for _, path, _ in shown), default=0)
        last = None
        for heading, path, what in shown:
            if heading is not None and heading != last:
                lines.append(f"  {heading}:")
                last = heading
            indent = "    " if heading is not None else "  "
            lines.append(f"{indent}{path.ljust(width)}  {what}")
        if rest > 0:
            lines.append(f"  … and {rest} more")
    lines += [
        f"{root.label}: not under git, not compared." for root in changes.unversioned
    ]
    return lines


def _what(f) -> str:
    """A changed file's class, as its line says it."""
    if f.reason:
        return f"{'deleted ' if f.deleted else ''}({f.reason})"
    asked = ", ".join(f.classes)
    return f"deleted → {asked}" if f.deleted else asked


def _every_test(changes, tests: int, named: int) -> str:
    """The line that says every test is counted, and why."""
    from ..discovery.uses import NO_CLASS

    files = changes.every_test
    n = len(files)
    if all(f.reason == NO_CLASS for f in files):
        why = f"{_count(n, 'file')} that no class stores changed"
    else:
        why = f"{_count(n, 'changed file')} cannot be asked as a class"
    line = f"{why}: every test is counted ({_count(tests, 'test')})"
    if named:
        line += (
            "; the class named is not asked"
            if named == 1
            else "; the classes named are not asked"
        )
    return line + "."


def _among(n: int, total: int) -> str:
    """``n`` of the ``total`` tests asked, ``n`` at least 1, in words:
    ``3 of 14 tests``, or ``the 1 test`` when one was asked."""
    return "the 1 test" if total == 1 else f"{n} of {total} tests"
