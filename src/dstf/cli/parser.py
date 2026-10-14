"""The command tree: every command, the flags each one reads, and the words
its ``--help`` prints.

Three rules hold here, and ``tests/test_cli_tree.py`` fails when one decays:

* **Groups are real sub-parsers** (``dstf spec add``, ``dstf manifest
  cleanup``): every action has its own ``--help``, its own positionals
  (required by the parser) and only its own flags.
* **A command offers exactly the flags it reads** (:data:`READS`, which
  the tree is built from). A flag asks for something: if the command, the
  action or the other flags on the line mean it will not happen, the
  command stops and says so (:data:`UNREAD`) — the command line's version
  of "a key nothing reads is an error". The one exception is a flag that
  asks for something NOT to happen (``--no-browser``): it is always
  accepted. The four flags that say where the project is may be written
  before the command as well as after it, once; one written before a
  command that does not read it is refused by
  :meth:`_Root.parse_known_args`, which is the only place that can see it.
  No node takes a prefix of a flag for the flag, and no flag or positional
  takes an empty value.
* **Every node explains itself**: a description, an example that parses,
  and help on every option and positional, with its default where it has
  one.

:func:`build_arg_parser` builds the tree without loading a plug-in or
reading a file: ``--help`` and ``--version`` must not wait for either.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import textwrap
from collections.abc import Callable
from pathlib import Path

from ..config import DEFAULT_COMPARISON_TOLERANCE, Config
from .author_cmd import DEFAULT_PORT
from .common import _VALID_REVIEW_FILTERS, _parse_review_filter, _split_filter_list
from .diag_cmds import EXPORT_FILES
from .run_cmds import REPORT_FILES
from .spec_cmds import SCHEMAS

# ---------------------------------------------------------------------------
# The parser classes
# ---------------------------------------------------------------------------


class _Node(argparse.ArgumentParser):
    """A command or a group of the tree. A group given no action (``dstf
    spec``), or one it has not, prints the group's whole help before the
    error: the actions are what the user needs to see.

    No node takes a prefix of a flag for the flag (``allow_abbrev``):
    ``compare --a`` was ``--accept``, and stored the baselines."""

    is_group = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **{**kwargs, "allow_abbrev": False})

    def error(self, message: str):
        if self.is_group:
            import sys

            self.print_help(sys.stderr)
            self.exit(2, f"\n{self.prog}: error: {message}\n")
        super().error(message)


class _Root(argparse.ArgumentParser):
    """The top-level parser. It knows the tree it carries — ``nodes`` (every
    parser by its command path: ``""``, ``"spec"``, ``"spec add"``),
    ``commands`` (the ones that run) — and checks, once the command line is
    parsed, what no sub-parser can: a flag written BEFORE a command that
    does not read it. What a command did not recognise is that command's
    error too, with its usage. As every node, it takes no prefix of a flag
    for the flag (:class:`_Node`)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **{**kwargs, "allow_abbrev": False})
        self.nodes: dict[str, argparse.ArgumentParser] = {"": self}
        self.commands: dict[str, argparse.ArgumentParser] = {}
        #: Per command: the project flags (by dest) it offers.
        self.reads: dict[str, frozenset[str]] = {}
        #: Per command: its one line of the top-level listing.
        self.summaries: dict[str, str] = {}
        #: The commands that may create a file of the project, and so write
        #: a default testing.json where none is found (``_Tree.command``).
        self.creates: set[str] = set()

    def parse_known_args(self, args=None, namespace=None):
        if args is None:
            import sys

            args = sys.argv[1:]
        args = list(args)
        self._refuse_misplaced(args)
        parsed, extras = super().parse_known_args(args, namespace)
        path = command_path(parsed)
        leaf = self.commands.get(path)
        if leaf is None:
            return parsed, extras
        twice = self._given_twice(args, parsed)
        if twice:
            leaf.error(twice)
        offered = self.reads[path]
        reads = ", ".join(PROJECT_FLAGS[d] for d in PROJECT_FLAGS if d in offered)
        # Written before the command, which does not read it: the top level
        # took it, and only here can it be seen. Written after it, it is
        # among what the command did not recognise.
        refused = [
            flag
            for dest, flag in PROJECT_FLAGS.items()
            if dest not in offered
            and (
                getattr(parsed, dest, None) is not None
                or any(e == flag or e.startswith(flag + "=") for e in extras)
            )
        ]
        if refused:
            leaf.error(
                f"{refused[0]} does not apply: `{leaf.prog}` does not read it "
                f"(of the flags that say where the project is, it reads "
                f"{reads or 'none'})"
            )
        if extras:
            # The command's own error and usage, not the top level's — whose
            # usage would offer the very flag being refused.
            leaf.error(f"unrecognized arguments: {' '.join(extras)}")
        refusal = _unread_beside(path, leaf, parsed)
        if refusal:
            leaf.error(refusal)
        return parsed, extras

    def _refuse_misplaced(self, argv: list[str]) -> None:
        """A flag of a command written before the command, or between a
        group and its action, said as where it goes — argparse took the
        flag's value for the command (``dstf --backend Python run``:
        ``invalid choice: 'Python'``) or listed it under a usage that
        offers it. Only a flag some command has is said so; any other is
        argparse's to call unrecognized."""
        commands = _subcommands(self).choices
        takes = self._values_taken()
        i = _find_word(takes, argv, 0, commands)
        if i < len(argv) and argv[i] not in commands:
            _invalid_choice(self, argv[i])
        path = argv[i] if i < len(argv) else "<command>"
        group = commands.get(path)
        if group is not None and group.is_group:
            actions = _subcommands(group).choices
            j = _find_word(takes, argv, i + 1, actions)
            if j < len(argv) and argv[j] not in actions:
                _invalid_choice(group, argv[j])
            path += f" {argv[j]}" if j < len(argv) else " <action>"
            self._say_misplaced(argv[i + 1 : j], path, allowed=group, at=group)
        self._say_misplaced(argv[:i], path, allowed=self, at=group or self)

    def _values_taken(self) -> dict[str, object]:
        """Every option of the tree that takes a value, with its ``nargs``.
        A flag written before the command may be any command's, so the
        word after it is its value by what that command says it takes."""
        return {
            s: a.nargs
            for node in self.nodes.values()
            for a in node._actions
            if a.nargs != 0 and not isinstance(a, argparse._SubParsersAction)
            for s in a.option_strings
        }

    def _given_twice(self, argv: list[str], parsed) -> str | None:
        """A project flag written before the command and after it, with two
        values: the later used to win, said only in the usage guide. Each
        is written once, on either side (the same flag twice on one side is
        argparse's: the last wins)."""
        end = _find_word(self._values_taken(), argv, 0, _subcommands(self).choices)
        before: dict[str, str] = {}
        i = 0
        while i < end:
            name, eq, value = argv[i].partition("=")
            dest = next((d for d, f in PROJECT_FLAGS.items() if f == name), None)
            if dest is not None and not eq and i + 1 < end:
                value, i = argv[i + 1], i + 1
            if dest is not None:
                before[dest] = value
            i += 1
        for dest, value in before.items():
            after = getattr(parsed, dest, None)
            if after != value:
                return (
                    f"{PROJECT_FLAGS[dest]} is given twice, {value} before the "
                    f"command and {after} after it: write it once"
                )
        return None

    def _say_misplaced(
        self,
        words: list[str],
        path: str,
        *,
        allowed: argparse.ArgumentParser,
        at: argparse.ArgumentParser,
    ) -> None:
        """The first of ``words`` that is a flag of some command and not of
        ``allowed`` (the parser whose place they are written in), as the
        error of ``path`` — or of ``at``, when ``path`` names no command."""
        for word in words:
            name = word.partition("=")[0]
            if not name.startswith("-") or name in _option_strings(allowed):
                continue
            having = [p for p, n in self.commands.items() if name in _option_strings(n)]
            if not having:
                continue
            (flag,) = {
                a.option_strings[-1]
                for a in self.commands[having[0]]._actions
                if name in a.option_strings
            }
            leaf = self.commands.get(path)
            if leaf is not None and name not in _option_strings(leaf):
                leaf.error(
                    f"{flag} does not apply: `dstf {path}` does not read it (it "
                    f"is a flag of: {', '.join(having)})"
                )
            (leaf or at).error(f"{flag} goes after the command: dstf {path} {flag} …")

    def format_help(self) -> str:
        """argparse's help, with the commands listed by what they are for
        (``COMMAND_GROUPS``) in place of its one flat list of the ten
        top-level names: every command that runs, one line each."""
        formatter = self._get_formatter()
        formatter.add_usage(self.usage, self._actions, self._mutually_exclusive_groups)
        formatter.add_text(self.description)
        for heading, paths in COMMAND_GROUPS:
            formatter.start_section(heading)
            formatter.add_arguments(
                [
                    argparse.Action(
                        [], dest=path, metavar=path, help=self.summaries[path]
                    )
                    for path in paths
                ]
            )
            formatter.end_section()
        for group in self._action_groups:
            formatter.start_section(group.title)
            formatter.add_text(group.description)
            formatter.add_arguments(
                [
                    action
                    for action in group._group_actions
                    if not isinstance(action, argparse._SubParsersAction)
                ]
            )
            formatter.end_section()
        formatter.add_text(self.epilog)
        return formatter.format_help()


def _subcommands(node: argparse.ArgumentParser) -> argparse._SubParsersAction:
    (sub,) = [a for a in node._actions if isinstance(a, argparse._SubParsersAction)]
    return sub


def _option_strings(node: argparse.ArgumentParser) -> set[str]:
    return {s for a in node._actions for s in a.option_strings}


def _invalid_choice(node: argparse.ArgumentParser, word: str) -> None:
    """``word``, where ``node``'s command or action is written, is none:
    argparse's own error, said of that word — not of a flag's value it
    would take for the command (``dstf --backend Python rn``)."""
    try:
        node._check_value(_subcommands(node), word)
    except argparse.ArgumentError as e:
        node.error(str(e))


def _find_word(takes: dict[str, object], argv: list[str], start: int, names) -> int:
    """The index of the first word from ``start`` on that is not an option
    or an option's value — the command, or a group's action, when the line
    names one of ``names`` there; ``len(argv)`` when there is none. An
    option of ``takes`` that must have a value takes the next word whatever
    it is (``--filter check run``: the command is ``run``); one whose value
    is optional takes the words up to a name of ``names`` (``-i failed
    run``)."""
    i = start
    while i < len(argv) and argv[i] != "--":
        word = argv[i]
        i += 1
        if not word.startswith("-"):
            return i - 1
        if "=" in word or word not in takes:
            continue
        nargs = takes[word]
        if nargs is None or isinstance(nargs, int):
            i += 1 if nargs is None else nargs
            continue
        while i < len(argv) and argv[i] not in names and not argv[i].startswith("-"):
            i += 1
            if nargs == "?":
                break
    return min(i, len(argv))


def command_path(args: argparse.Namespace) -> str:
    """The command a parsed command line names, as it is typed: ``run``,
    ``spec add``. The dispatch table and the tree are keyed by it."""
    return " ".join(
        part
        for part in (getattr(args, "command", None), getattr(args, "action", None))
        if part
    )


@dataclasses.dataclass(frozen=True)
class Unread:
    """Flags a command offers and reads only beside another flag — or
    never beside one. ``with_`` rows: ``flags`` are not read when any of
    ``beside`` is on the line (unless one of ``unless`` is too). Without
    rows (``with_=False``): ``flags`` are read only when one of ``beside``
    is. ``why`` is the reason, as the refusal and the help say it."""

    command: str
    flags: tuple[str, ...]
    beside: tuple[str, ...]
    why: str
    with_: bool = True
    unless: tuple[str, ...] = ()


#: A flag asks for something. When the command, the action or the other
#: flags on the line mean it will not happen, the command line is refused
#: (:func:`_unread_beside`), and the help says so (:func:`_say_unread`).
#: A flag that asks for something NOT to happen (``--no-browser``) is
#: never in it: it is always accepted.
UNREAD: tuple[Unread, ...] = (
    Unread(
        "run",
        ("report", "report_format", "output"),
        ("accept",),
        "an accept stores the results and writes no report",
    ),
    Unread(
        "run",
        ("merge",),
        ("accept",),
        "an accept stores the results of the tests this run simulated, and no others",
    ),
    Unread(
        "run",
        ("tolerance", "default_points"),
        ("accept",),
        "an accept scores nothing (with --rerun they choose the tests to run again)",
        unless=("rerun",),
    ),
    Unread(
        "run",
        ("report", "report_format", "output"),
        ("interactive",),
        "a review writes no report",
    ),
    Unread(
        "run",
        ("merge",),
        ("filter", "package", "rerun"),
        "without them every test runs, and there is nothing to merge in",
        with_=False,
    ),
    Unread(
        "compare",
        ("tolerance", "default_points", "report", "report_format", "output"),
        ("accept",),
        "an accept stores the results, scores nothing and writes no report",
    ),
    Unread(
        "compare",
        ("parallel",),
        ("accept",),
        "it is the number of threads that render --report's pages, and an "
        "accept writes none",
    ),
    Unread(
        "compare",
        ("parallel",),
        ("report",),
        "it is the number of threads that render --report's pages",
        with_=False,
    ),
    *(
        Unread(
            command,
            ("output",),
            ("report_format",),
            "it names the file junit or html writes, and the console format "
            "(the default) writes none",
            with_=False,
        )
        for command in ("run", "compare")
    ),
    Unread(
        "baseline export",
        ("work_dir",),
        ("output",),
        "the work directory is where the file goes when --output names none",
    ),
    Unread(
        "manifest cleanup",
        ("test_spec", "work_dir"),
        ("orphans",),
        "only --orphans reads the tests and cleans the work directory",
        with_=False,
    ),
    Unread(
        "check",
        ("reference_root",),
        ("config",),
        "check reads it only to look for testing.json in it",
    ),
)


def _flag(node: argparse.ArgumentParser, dest: str) -> argparse.Action:
    (action,) = [a for a in node._actions if a.dest == dest and a.option_strings]
    return action


def _name(node: argparse.ArgumentParser, dest: str) -> str:
    """A flag as the help and the refusals name it: its long spelling."""
    return _flag(node, dest).option_strings[-1]


def _names(node: argparse.ArgumentParser, dests: tuple[str, ...], word: str) -> str:
    names = [_name(node, d) for d in dests]
    return (
        names[0] if len(names) == 1 else f"{', '.join(names[:-1])} {word} {names[-1]}"
    )


def _on_the_line(node: argparse.ArgumentParser, args, dest: str) -> bool:
    """Whether the command line gave ``dest`` — a value other than the one
    the parser fills in (``--report-format console`` is the default)."""
    value = getattr(args, dest, None)
    default = _flag(node, dest).default
    return (
        value is not None
        and value is not False
        and (default is argparse.SUPPRESS or value != default)
    )


def _unread_beside(path: str, node: argparse.ArgumentParser, args) -> str | None:
    """The first flag on the line that :data:`UNREAD` says will not be read
    beside the others, as one sentence naming both; None when there is
    none."""
    for row in UNREAD:
        if row.command != path:
            continue
        beside = [d for d in row.beside if _on_the_line(node, args, d)]
        if row.with_ and (
            not beside or any(_on_the_line(node, args, d) for d in row.unless)
        ):
            continue
        if not row.with_ and beside:
            continue
        for dest in row.flags:
            if not _on_the_line(node, args, dest):
                continue
            flag = _flag(node, dest)
            given = _name(node, dest)
            if flag.nargs != 0:
                given += f" {getattr(args, dest)}"
            where = (
                f"with {_name(node, beside[0])}"
                if row.with_
                else f"without {_names(node, row.beside, 'or')}"
            )
            return f"{given} does not apply {where}: {row.why}"
    return None


def _say_unread(nodes: dict[str, argparse.ArgumentParser]) -> None:
    """Each :data:`UNREAD` row in the help: on the flag that decides, the
    flags it is not read with (``--accept``: "Not with --report, …"); on a
    flag read only beside another, which ("Only with --orphans")."""
    for row in UNREAD:
        node = nodes[row.command]
        if row.with_:
            unless = f" (unless {_names(node, row.unless, 'or')})" if row.unless else ""
            text = f"Not with {_names(node, row.flags, 'or')}{unless}: {row.why}."
            for dest in row.beside:
                _flag(node, dest).help += " " + text
        else:
            text = f"Only with {_names(node, row.beside, 'or')}: {row.why}."
            for dest in row.flags:
                _flag(node, dest).help += " " + text


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


def _whole(
    what: str, *, minimum: int, maximum: int | None = None
) -> Callable[[str], int]:
    """An argparse ``type=`` for a whole number in a range: a value outside
    it is a parser error that says what is allowed. (A truthiness test
    downstream used to DROP ``--timeout 0`` and ``--parallel 0`` and run on
    the default.)"""
    allowed = (
        f"a whole number of {minimum} or more"
        if maximum is None
        else f"a whole number from {minimum} to {maximum}"
    )

    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"{text!r}: must be {allowed} ({what})"
            ) from None
        if value < minimum or (maximum is not None and value > maximum):
            raise argparse.ArgumentTypeError(f"{value}: must be {allowed} ({what})")
        return value

    return parse


def _a_value(parse: Callable[[str], object] | None) -> Callable[[str], object]:
    """The one argparse ``type=`` every flag that takes a value and every
    positional goes through (:func:`build_arg_parser` lays it over each
    one's own): an empty value is the parser's error. A truthiness test
    read ``--backend ""`` as not given, ``--work-dir ""`` as the current
    directory, and ``--filter ""`` selected every test — under ``compare
    --accept``, every baseline."""

    def value(text: str):
        if not text.strip():
            raise argparse.ArgumentTypeError("an empty value names nothing")
        return text if parse is None else parse(text)

    # argparse names a type in its "invalid <type> value" error
    value.__name__ = getattr(parse, "__name__", "value")
    return value


def _filter_arg(value: str) -> str:
    """``--filter``: patterns that are all empty (``,``) select every test,
    as an empty filter did. A ``@FILE`` is read when the tests are."""
    if not value.startswith("@") and not _split_filter_list(value):
        raise argparse.ArgumentTypeError(
            f"{value!r} names nothing: there is no pattern between its commas"
        )
    return value


def _output_file(text: str) -> str:
    """``--output``, on every command that has it: a file that can be
    written — not a directory, in a directory that is there, and one this
    user may write (the file, when it is there; else its directory). Said
    when the command line is read, before any work: a run of an hour ended
    by failing to write its report (a traceback), and ``spec schema -o
    missing/x.json`` by a FileNotFoundError."""
    path = Path(text)
    if path.is_dir():
        raise argparse.ArgumentTypeError(f"{text} is a directory: name a file to write")
    parent = path.parent
    if not parent.is_dir():
        raise argparse.ArgumentTypeError(
            f"{text}: the directory {parent} does not exist"
        )
    if path.exists():
        if not os.access(path, os.W_OK):
            raise argparse.ArgumentTypeError(f"{text} cannot be written")
    elif not os.access(parent, os.W_OK | os.X_OK):
        raise argparse.ArgumentTypeError(
            f"{text}: the directory {parent} cannot be written"
        )
    return text


def _review_filter_arg(value: str) -> str:
    """argparse ``type=`` callback for ``-i`` / ``--rerun`` category lists.

    review 2026-07-06 finding 58: validate at parse time so a typo like
    ``-i sim_failed`` aborts in under a second with the valid-categories
    message (argparse exit code 2) — not after the full simulation run.
    """
    _parse_review_filter(value)  # raises argparse.ArgumentTypeError on typos
    return value


class _BackendNames:
    """``check``'s choices: the registered backends' names and every name
    that starts with one — an entry of ``backends`` is named so, and which
    entries a project has is its ``testing.json``'s to say, not the
    parser's (``cmd_check``). They are asked for when an argument is
    checked against them and not when the parser is built — a plug-in
    registers its backend as the plug-ins load, which `--help` and
    `--version` must not wait for (nor fail on).

    Only the check itself loads them. argparse also ITERATES the choices —
    to print the command's help, which names none of them, and to list them
    in an invalid-choice error, which comes after the check — so iterating
    lists what is registered by then and loads nothing."""

    def __contains__(self, name: object) -> bool:
        from ..backends import backend_family
        from ..plugins import load_plugins

        load_plugins()
        return isinstance(name, str) and backend_family(name) in list(self)

    def __iter__(self):
        from ..backends import backend_names

        return iter(backend_names())


def _config_default(name: str):
    """A Config field's default: the value a flag's help states."""
    (field,) = (f for f in dataclasses.fields(Config) if f.name == name)
    return field.default


_CATEGORIES = ", ".join(sorted(_VALID_REVIEW_FILTERS - {"all"}))
_BACKENDS = "Dymola, OpenModelica, FMPy, Julia, Python, or a plug-in's"

# ---------------------------------------------------------------------------
# The flags — one definition each
# ---------------------------------------------------------------------------

#: The flags that say where the project is, by dest. A command offers the
#: ones it reads; each may be written before the command too.
PROJECT_FLAGS = {
    "config": "--config",
    "source_path": "--source-path",
    "reference_root": "--reference-root",
    "test_spec": "--test-spec",
}

_WORK_DIR_DEFAULT = (
    "Default: testing.json's work_dir; without one, "
    "testing_output/<library>/<backend>/<os> under the current directory."
)
_BACKEND = (
    f"The backend, by its entry of testing.json's backends map or by its "
    f"name ({_BACKENDS})."
)
_BACKEND_DEFAULT = (
    "Default: testing.json's backend; without one, the first entry of its "
    "backends map that is installed here."
)
#: What ``--backend`` selects for a command that only reads or writes
#: stored references.
_PARTITION = (
    f"{_BACKEND} It names the partition of the reference root this command "
    f"works on: <reference_root>/<Backend>/<os>/. {_BACKEND_DEFAULT}"
)

#: Every flag more than one command offers: its option strings and its
#: ``add_argument`` keywords. ``{…}`` in a help is filled per command
#: (:func:`_offer`).
_FLAGS: dict[str, tuple[tuple[str, ...], dict]] = {
    "config": (
        ("--config",),
        dict(
            metavar="FILE",
            help="The project's testing.json, or the directory holding it. "
            "Default: the one in the library's parent directory, the library, "
            "the reference root or the current directory. {no_config}",
        ),
    ),
    "source_path": (
        ("--source-path",),
        dict(
            metavar="DIR",
            help="The library under test: a Modelica package directory (the "
            "one holding package.mo), an FMU directory, a Julia project or a "
            "directory of Python tests. Default: testing.json's source_path; "
            "without one, a Modelica package found from the current directory.",
        ),
    ),
    "reference_root": (
        ("--reference-root",),
        dict(
            metavar="DIR",
            help="Where the reference results are kept. Default: testing.json's "
            "reference_root; without one, testing.json's own directory when "
            "that is named ReferenceResults, else Resources/ReferenceResults "
            "beside the library.",
        ),
    ),
    "test_spec": (
        ("--test-spec",),
        dict(
            metavar="FILE",
            help="The test_spec.json {spec_use}. Default: "
            "testing.json's test_spec; without one, test_spec.json in the "
            "reference root.",
        ),
    ),
    "backend": (("--backend",), dict(metavar="NAME", help=_PARTITION)),
    "backend_path": (
        ("--backend-path",),
        dict(
            metavar="FILE",
            help="The backend's executable, in place of the one testing.json "
            "names or the search finds. {backend_path_note}",
        ),
    ),
    "work_dir": (
        ("--work-dir",),
        dict(metavar="DIR", help="{work_dir_use} " + _WORK_DIR_DEFAULT),
    ),
    "filter": (
        ("--filter",),
        dict(
            type=_filter_arg,
            metavar="PATTERNS",
            help="Only the tests whose model id matches. ONE argument: a glob "
            "('MyLib.Examples.*'), a comma-separated list of globs "
            "('Lib.A,Lib.B*'), or @FILE naming a file with one pattern per "
            "line (# starts a comment). A comma inside a [] character class "
            "belongs to the class ('Test[A,B]*' is one pattern). Matching is "
            "case-sensitive. Default: every test.",
        ),
    ),
    "package": (
        ("--package",),
        dict(
            metavar="PREFIX",
            help="Only the tests of this package: the model with this id and "
            "every model under it ('MyLib.Fluid' selects MyLib.Fluid.Pipe, not "
            "MyLib.FluidExtra.Pipe). Default: every package.",
        ),
    ),
    "tolerance": (
        ("--tolerance",),
        dict(
            type=float,
            metavar="NUMBER",
            help="The project's base tolerance for this command: the nrmse "
            "ceiling of a variable no test, baseline or override gives one. "
            "0 is the strictest value, not 'unset'. Default: testing.json's "
            f"tolerance; without one, {DEFAULT_COMPARISON_TOLERANCE:g}.",
        ),
    ),
    "default_points": (
        ("--default-points",),
        dict(
            action="store_true",
            help="Score a variable that names no mode of its own by its final "
            "value (the points mode with no points listed) instead of nrmse.",
        ),
    ),
    "report_format": (
        ("--report-format",),
        dict(
            choices=list(REPORT_FILES),
            default=next(iter(REPORT_FILES)),
            help="The report to write besides the verdict table this command "
            "prints: junit (a JUnit XML file) or html (the dashboard page, as "
            "one file), at --output. Default: console, which writes no file.",
        ),
    ),
    "output": (("-o", "--output"), dict(type=_output_file, metavar="FILE")),
    "report": (
        ("--report",),
        dict(
            action="store_true",
            help="Also write a page per test, with its plots and its criteria, "
            "under <work dir>/reports/, linked from <work dir>/dashboard.html "
            "(which is written either way). It takes no value.",
        ),
    ),
    "no_browser": (
        ("--no-browser",),
        dict(
            action="store_true",
            help="Open no browser tab on {page}; its link is printed either way.",
        ),
    ),
    "format": (("--format",), dict(choices=list(EXPORT_FILES))),
}

_NO_CONFIG_WRITES = (
    "When none is found, this command writes a default one beside the "
    "library (in the reference root when --reference-root is given)."
)
_NO_CONFIG_READS = (
    "When none is found, the project is read on defaults and none is written."
)


def _offer(parser, name: str, *, fill: dict | None = None, **changes) -> None:
    """Add the flag ``name`` to ``parser`` (or to an argument group of one)
    from its one definition. ``changes`` are ``add_argument`` keywords laid
    over it; ``fill`` fills the ``{…}`` fields of its help."""
    strings, defined = _FLAGS[name]
    kwargs = {**defined, **changes}
    if "{" in kwargs["help"]:
        kwargs["help"] = kwargs["help"].format(**(fill or {})).strip()
    parser.add_argument(*strings, **kwargs)


def _examples(*lines: str) -> str:
    return "examples:\n" + "\n".join(f"  {line}" for line in lines)


def _text(text: str) -> str:
    """A description as it is printed: each paragraph filled to 78 columns
    (the formatter leaves a description and an epilog as written, for the
    examples' sake)."""
    paragraphs = textwrap.dedent(text).strip().split("\n\n")
    return "\n\n".join(
        # A name with a hyphen in it (a package, a flag) stays whole.
        textwrap.fill(" ".join(p.split()), 78, break_on_hyphens=False)
        for p in paragraphs
    )


class _Formatter(argparse.RawDescriptionHelpFormatter):
    """argparse's formatter, keeping a description and an epilog as written
    and filling an option's help so that a name with a hyphen in it
    (``--report-format``, ``case-sensitive``) stays whole, as :func:`_text`
    does for a description."""

    def _split_lines(self, text: str, width: int) -> list[str]:
        return textwrap.wrap(" ".join(text.split()), width, break_on_hyphens=False)


class _Tree:
    """The tree as it is built: adds a command or a group under a parent,
    with the same formatter and the same bookkeeping for each."""

    def __init__(self, root: _Root):
        self.root = root

    def subparsers(self, parent: argparse.ArgumentParser, **kwargs):
        return parent.add_subparsers(parser_class=_Node, **kwargs)

    def _add(self, sub, path: str, *, summary: str, description: str, examples):
        node = sub.add_parser(
            path.rsplit(" ", 1)[-1],
            help=summary,
            description=_text(description),
            epilog=_examples(*examples),
            formatter_class=_Formatter,
        )
        self.root.nodes[path] = node
        return node

    def group(self, sub, path: str, *, summary: str, description: str, examples):
        """A group: it runs nothing itself, and requires one of its actions."""
        node = self._add(
            sub, path, summary=summary, description=description, examples=examples
        )
        node.is_group = True
        return node, self.subparsers(
            node, dest="action", title="actions", metavar="<action>", required=True
        )

    def command(
        self,
        sub,
        path: str,
        *,
        summary: str,
        description: str,
        examples,
        creates: bool = False,
        spec_use: str = "this command reads",
        config_help: str | None = None,
    ):
        """A command that runs. The project flags it reads (:data:`READS`)
        are offered here, and ONLY they are accepted before it.
        ``spec_use`` is this command's word in those flags' help,
        ``config_help`` its own help for ``--config``.

        ``creates`` is the command's declaration that it may create a file
        of the project — a baseline, a spec entry, a soft check, a
        companion. ONE declaration, read twice: such a command writes a
        default testing.json where it finds none (``_build_config``), and
        its ``--config`` help says so. The default is that it may not: a
        command that reads, lists, exports or removes starts no project."""
        node = self._add(
            sub, path, summary=summary, description=description, examples=examples
        )
        node.set_defaults(may_create_project=creates)
        if creates:
            self.root.creates.add(path)
        no_config = _NO_CONFIG_WRITES if creates else _NO_CONFIG_READS
        project = [dest for dest in PROJECT_FLAGS if dest in READS[path]]
        for dest in project:
            own = {"help": config_help} if dest == "config" and config_help else {}
            # SUPPRESS is load-bearing: a sub-parser parses into a fresh
            # namespace and copies EVERY attribute of it onto the top-level
            # one, so a plain default here would erase a value given before
            # the command. The top-level copy is what guarantees the
            # attribute exists at all.
            _offer(
                node,
                dest,
                default=argparse.SUPPRESS,
                fill={"no_config": no_config, "spec_use": spec_use},
                **own,
            )
        self.root.commands[path] = node
        self.root.reads[path] = frozenset(project)
        self.root.summaries[path] = summary
        return node


#: The top-level help's listing: the commands by what they are for.
COMMAND_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("run and score", ("discover", "run", "compare")),
    ("write criteria", ("author", "spec add", "spec update", "spec schema")),
    (
        "baselines and the data beside them",
        (
            "baseline export",
            "soft-check add",
            "soft-check list",
            "soft-check remove",
            "companion add",
            "companion list",
            "companion freeze",
            "companion remove",
            "manifest show",
            "manifest dump",
            "manifest cleanup",
        ),
    ),
    ("this machine", ("check",)),
)

_ALL = ("config", "source_path", "reference_root", "test_spec")
_STORE = ("config", "source_path", "reference_root")
_SELECT = ("filter", "package")
_REPORTS = ("report_format", "output", "report", "no_browser")

#: What each command reads: every flag it offers, by its dest. The tree is
#: built from it — a command offers the project flags named here, and
#: :func:`build_arg_parser` refuses to build a command that offers any flag
#: this table does not name, or names one it does not offer. That its
#: handler reads each one is ``tests/test_cli_tree.py``'s to prove.
READS: dict[str, tuple[str, ...]] = {
    "discover": (*_ALL, *_SELECT),
    "run": (
        *_ALL,
        *_SELECT,
        "tolerance",
        "default_points",
        *_REPORTS,
        "interactive",
        "accept",
        "backend",
        "backend_path",
        "work_dir",
        "parallel",
        "batch",
        "timeout",
        "merge",
        "rerun",
    ),
    "compare": (
        *_ALL,
        *_SELECT,
        "tolerance",
        "default_points",
        *_REPORTS,
        "backend",
        "backend_path",
        "work_dir",
        "parallel",
        "accept",
    ),
    "author": ("config", *_SELECT, "port", "no_browser", "work_dir", "backend"),
    "check": _STORE,
    "spec add": (*_ALL, "variables"),
    "spec update": (*_ALL, "backend"),
    "spec schema": ("output",),
    "baseline export": (*_STORE, "format", "output", "backend", "work_dir"),
    "soft-check add": (*_STORE, "backend"),
    "soft-check list": (*_STORE, "backend"),
    "soft-check remove": (*_STORE, "backend"),
    "companion add": (*_STORE, "format", "variable_map", "backend"),
    "companion list": (*_STORE, "backend"),
    "companion freeze": (*_STORE, "backend"),
    "companion remove": (*_STORE, "backend"),
    "manifest show": (*_STORE, "show_obsolete", "backend"),
    "manifest dump": (*_STORE, "backend", "work_dir"),
    "manifest cleanup": (*_ALL, "orphans", "apply", "backend", "work_dir"),
}


def build_arg_parser() -> argparse.ArgumentParser:
    """Construct the top-level CLI parser — the whole command tree, without
    loading a plug-in or reading a file. Tests and the documentation's
    generator walk it (``parser.nodes`` holds every node by its command
    path); :func:`dstf.cli.main.main` parses with it and dispatches."""
    from .. import __version__ as _dstf_version

    root = _Root(
        prog="dstf",
        description=_text(
            """
            Dynamic Systems Testing Framework: regression and unit testing
            of time-dependent system behaviour. It finds a library's tests,
            simulates them with a backend, scores each result against a
            stored baseline, and reports.

            A project is a library, a testing.json that names it, and a
            directory of reference results. `dstf <command> --help` says
            what a command does, what it writes, and every flag it reads.
            """
        ),
        epilog=_examples(
            "dstf run --config testing.json",
            "dstf --config testing.json run --filter 'MyLib.Examples.*' --report",
            "dstf compare --config testing.json --report-format junit -o results.xml",
            "dstf spec add MyLib.Examples.Rod --config testing.json --variables 'rod.T*'",
        ),
        formatter_class=_Formatter,
    )
    root.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {_dstf_version}",
        help="Print the installed dstf version and exit.",
    )
    where = root.add_argument_group(
        "where the project is",
        _text(
            "These may be written here, before the command, or after it. "
            "Each is written once, on either side. A command that does not "
            "read one refuses it."
        ),
    )
    for dest in PROJECT_FLAGS:
        _offer(
            where,
            dest,
            default=None,
            fill={"no_config": "", "spec_use": "a command reads or writes"},
        )
    tree = _Tree(root)
    top = tree.subparsers(root, dest="command", title="commands", metavar="<command>")

    _add_discover(tree, top)
    _add_run(tree, top)
    _add_compare(tree, top)
    _add_author(tree, top)
    _add_check(tree, top)
    _add_spec(tree, top)
    _add_baseline(tree, top)
    _add_soft_check(tree, top)
    _add_companion(tree, top)
    _add_manifest(tree, top)
    _say_unread(root.commands)
    for path, node in root.commands.items():
        offered = {a.dest for a in node._actions if a.option_strings} - {"help"}
        if offered != set(READS[path]):
            raise AssertionError(
                f"`dstf {path}` offers {sorted(offered)} and reads "
                f"{sorted(READS[path])} (cli.parser.READS)"
            )
    for node in root.nodes.values():
        for action in node._actions:
            if _takes_a_value(action):
                action.type = _a_value(action.type)
    return root


def _takes_a_value(action: argparse.Action) -> bool:
    """A flag that takes a value, or a positional — not a switch, ``--help``
    or the sub-command."""
    return action.nargs != 0 and not isinstance(
        action, argparse._SubParsersAction | argparse._HelpAction
    )


# ---------------------------------------------------------------------------
# run and score
# ---------------------------------------------------------------------------


def _offer_selection(node: argparse.ArgumentParser) -> None:
    _offer(node, "filter")
    _offer(node, "package")


def _offer_scoring(node: argparse.ArgumentParser) -> None:
    _offer(node, "tolerance")
    _offer(node, "default_points")


def _offer_reports(node: argparse.ArgumentParser) -> None:
    files = " or ".join(
        f"{name} ({fmt})" for fmt, name in REPORT_FILES.items() if name is not None
    )
    _offer(node, "report_format")
    _offer(
        node,
        "output",
        default=None,
        help="The file --report-format junit or html writes. Default: "
        f"{files}, in the current directory.",
    )
    _offer(node, "report")
    _offer(node, "no_browser", fill={"page": "the dashboard or a report page"})


def _add_discover(tree: _Tree, top) -> None:
    node = tree.command(
        top,
        "discover",
        summary="List the tests the project declares",
        description="""
            List the project's tests: every model a recognizer finds in the
            library's source and every entry of test_spec.json, each with
            what it tracks, its stop time, its solver and where it was
            declared. Nothing is simulated and nothing is written.

            Exit code: 0, or 1 when no test is found.
            """,
        examples=(
            "dstf discover --config testing.json",
            "dstf discover --config testing.json --filter 'MyLib.Examples.*'",
            "dstf discover --source-path MyLib --test-spec refs/test_spec.json",
        ),
    )
    _offer_selection(node)


def _add_run(tree: _Tree, top) -> None:
    node = tree.command(
        top,
        "run",
        summary="Simulate the tests and score them against baselines",
        description="""
            Simulate the project's tests with its backend and score each
            result against its stored baseline. It writes the results,
            dashboard.html (opened in a browser tab as the run starts, and
            live while it runs), verdicts.json and status.json to the work
            directory, and prints one verdict per test.

            With --accept the results are stored as the new baselines
            instead of being scored; with -i each test is shown and accepted
            or skipped by hand.

            Exit code: 0 when every test that ran passed; 1 when a test
            failed, failed to simulate or timed out, when nothing ran at
            all, or when an accept skipped a test; 2 for a config error.
            """,
        examples=(
            "dstf run --config testing.json",
            "dstf run --config testing.json --filter 'MyLib.Examples.*' --parallel 4 --report",
            "dstf run --config testing.json --accept",
            "dstf run --config testing.json --rerun failed,sim-failed --report",
            "dstf run --config testing.json --report-format junit -o results.xml --no-browser",
            "dstf run --config testing.json -i failed",
        ),
        creates=True,
    )
    _offer_selection(node)
    _offer_scoring(node)
    _offer_reports(node)
    # review 2026-07-06 finding 61: review-then-accept (-i) and
    # accept-everything (--accept) are mutually exclusive intents — make
    # combining them a hard argparse error instead of silently letting
    # --accept win and skipping the review the user asked for.
    accept_or_review = node.add_mutually_exclusive_group()
    accept_or_review.add_argument(
        "-i",
        "--interactive",
        nargs="?",
        const="all",
        default=None,
        metavar="CATEGORIES",
        type=_review_filter_arg,  # review 2026-07-06 finding 58: parse-time check
        help="After the run, review the tests one by one in the terminal: "
        "accept a result as the baseline, skip it, or plot it. CATEGORIES is "
        f"ONE comma-separated argument of: {_CATEGORIES}. Default, with no "
        "value: every test. Not with --accept.",
    )
    accept_or_review.add_argument(
        "--accept",
        action="store_true",
        help="Store this run's results as the new baselines in the reference "
        "root, and score nothing. A test whose simulation failed is skipped "
        "and named, and the exit code is then 1. Not with -i.",
    )
    _offer(
        node,
        "backend",
        help=f"{_BACKEND} It is the simulator the tests run with, and the "
        "partition of the reference root their baselines are read from: "
        f"<reference_root>/<Backend>/<os>/. {_BACKEND_DEFAULT}",
    )
    _offer(node, "backend_path", fill={"backend_path_note": ""})
    _offer(
        node,
        "work_dir",
        fill={
            "work_dir_use": "Where the run's results, dashboard.html and reports are written."
        },
    )
    node.add_argument(
        "--parallel",
        type=_whole("simulation workers", minimum=1),
        metavar="N",
        help="The number of SIMULATION workers: tests run N at a time, one "
        "simulator process each where the backend drives one. (On `compare` "
        f"the same flag means report-rendering threads.) Default: "
        f"{_config_default('parallel')}.",
    )
    node.add_argument(
        "--batch",
        action="store_true",
        help="Run through the backend's script mode instead of its workers "
        "that stay alive, which are the default: Dymola by .mos scripts, "
        "OpenModelica by one omc script per test, Julia by one julia process "
        "per test. Use it when a backend's worker interface is missing (the "
        "error that says so points here). An error on a backend that has no "
        "such workers (FMPy, Python).",
    )
    node.add_argument(
        "--timeout",
        type=_whole("seconds", minimum=1),
        metavar="SECONDS",
        help="How long one test may simulate before it is stopped and "
        f"reported as timed out. Default: {_config_default('timeout')}.",
    )
    node.add_argument(
        "--merge",
        action="store_true",
        help="Simulate the selected tests only, but score and report every "
        "test, the others on the results their last run left in the work "
        "directory.",
    )
    node.add_argument(
        "--rerun",
        nargs="?",
        const="failed",
        default=None,
        metavar="CATEGORIES",
        type=_review_filter_arg,  # review 2026-07-06 finding 58: parse-time check
        help="Simulate again only the tests the last run left in these "
        f"categories — ONE comma-separated argument of: {_CATEGORIES} — and "
        "report the whole suite (it implies --merge). Default, with no "
        "value: failed. Exit 0 when no test is in them.",
    )


def _add_compare(tree: _Tree, top) -> None:
    node = tree.command(
        top,
        "compare",
        summary="Score the last run's results again, without simulating",
        description="""
            Score the results the last run left in the work directory
            against the stored baselines, without simulating anything. It
            writes dashboard.html, verdicts.json and each test's verdict in
            status.json to the work directory, and prints one verdict per
            test. Use it after changing a tolerance or a criterion, or to
            write a report of a run that is already there.

            With --accept those results are stored as the new baselines
            instead of being scored.

            Exit code: as `dstf run`.
            """,
        examples=(
            "dstf compare --config testing.json",
            "dstf compare --config testing.json --report",
            "dstf compare --config testing.json --report-format html -o report.html",
            "dstf compare --config testing.json --tolerance 0.01 --filter 'MyLib.Fluid.*'",
        ),
        creates=True,
    )
    _offer_selection(node)
    _offer_scoring(node)
    _offer_reports(node)
    _offer(
        node,
        "backend",
        help=f"{_BACKEND} It names the partition of the reference root the "
        "baselines are read from: <reference_root>/<Backend>/<os>/. Default: "
        "the backend the last run in the work directory recorded; without "
        "one, as `dstf run` chooses.",
    )
    _offer(
        node,
        "backend_path",
        fill={
            "backend_path_note": "This command starts no simulator: the flag "
            "matters only to a backend that cannot be loaded without its "
            "executable (Julia, Python)."
        },
    )
    _offer(
        node,
        "work_dir",
        fill={"work_dir_use": "The work directory of the run to score."},
    )
    node.add_argument(
        "--parallel",
        type=_whole("rendering threads", minimum=1),
        metavar="N",
        help="The number of threads for --report RENDERING (the pages per "
        "test); scoring itself is fast and is not threaded. (On `run` the "
        f"same flag means simulation workers.) Default: "
        f"{_config_default('parallel')}.",
    )
    node.add_argument(
        "--accept",
        action="store_true",
        help="Store the last run's results as the new baselines in the "
        "reference root, without simulating again, and score nothing. A test "
        "the run recorded as failed or timed out is skipped and named, and "
        "the exit code is then 1.",
    )


# ---------------------------------------------------------------------------
# write criteria
# ---------------------------------------------------------------------------


def _add_author(tree: _Tree, top) -> None:
    node = tree.command(
        top,
        "author",
        summary="Open the authoring tool on the last run, in a browser",
        description="""
            Open the authoring tool: a page in the browser over the last
            run, where a test's criteria are edited beside the plots they
            score, results are accepted as baselines and tests are queued
            to run again. It serves on 127.0.0.1 until its Stop server, and
            writes test_spec.json (on Save), baselines (on Accept) and the
            work directory (when it runs a test).

            It needs the [author] extra:
            pip install 'dynamic-systems-testing-framework[author]'.
            """,
        examples=(
            "dstf author --config testing.json",
            "dstf author --config testing.json --port 8090 --no-browser",
        ),
        # Its own words: no testing.json is searched for, and the other
        # three flags are not this command's.
        config_help="The project's testing.json. Required: the tool reads "
        "the library, the reference results and test_spec.json from it and "
        "saves the spec it names, so none of the three can be named here "
        "instead.",
    )
    _offer_selection(node)
    node.add_argument(
        "--port",
        type=_whole("a TCP port", minimum=1, maximum=65535),
        default=DEFAULT_PORT,
        metavar="N",
        help=f"The port to serve on, on 127.0.0.1 only. Default: {DEFAULT_PORT}.",
    )
    _offer(node, "no_browser", fill={"page": "the tool"})
    _offer(
        node,
        "work_dir",
        fill={
            "work_dir_use": "The work directory of the run to open, and where a "
            "test queued from the tool runs."
        },
    )
    _offer(
        node,
        "backend",
        help=f"{_BACKEND} It is the simulator a queued test runs with, and "
        "the partition of the reference root the baselines are read from "
        f"and accepted into. {_BACKEND_DEFAULT}",
    )


def _add_spec(tree: _Tree, top) -> None:
    group, sub = tree.group(
        top,
        "spec",
        summary="test_spec.json: add a test, patch it, print a schema",
        description="""
            The commands that write or describe test_spec.json, the file a
            project declares its tests and their criteria in.
            """,
        examples=(
            "dstf spec add MyLib.Examples.Rod --config testing.json --variables 'rod.T*'",
            "dstf spec update spec_patch.json --config testing.json",
            "dstf spec schema -o test_spec.schema.json",
        ),
    )

    node = tree.command(
        sub,
        "spec add",
        summary="Add a test to test_spec.json",
        description="""
            Add a test to test_spec.json: an entry for the model, tracking
            the variables named. The file is created when it is not there.
            For a model that already has an entry the command asks first,
            and on y replaces that entry's variables and nothing else of it.

            Without --variables the test is simulate-only: it passes when
            its simulation completes.
            """,
        examples=(
            "dstf spec add MyLib.Examples.Rod --config testing.json --variables 'rod.T*' pump.m_flow",
            "dstf spec add MyLib.Examples.Smoke --config testing.json",
        ),
        creates=True,
        spec_use="this command writes",
    )
    node.add_argument(
        "model_id",
        metavar="model",
        help="The test's model, by the id the backend runs it under: a "
        "Modelica class (MyLib.Examples.Rod), an FMU's name, a Julia or "
        "Python test's name.",
    )
    node.add_argument(
        "--variables",
        nargs="*",
        default=None,
        metavar="PATTERN",
        help="The variables to track, by name or glob, SPACE-separated — one "
        "argument each, after the model ('rod.T*' pump.m_flow). Quote a "
        "pattern holding * or [ so the shell leaves it alone. Default: none, "
        "a simulate-only test.",
    )

    node = tree.command(
        sub,
        "spec update",
        summary="Apply a JSON-Patch envelope to test_spec.json",
        description="""
            Apply a patch envelope to test_spec.json: RFC 6902 JSON-Patch
            operations per model, as the authoring tool's Export writes
            them (one model's or several models'). Every patched entry is
            checked as the file is when it is read, and the file is written
            only when all of them hold; a patch that would write a key
            nothing reads, or a criterion that could never be scored, is
            refused and nothing is written.

            Exit code: 0 when applied (or the envelope is empty), 1 when
            the patch is refused or the file cannot be read.
            """,
        examples=("dstf spec update spec_patch.json --config testing.json",),
        creates=True,
        spec_use="this command writes",
    )
    node.add_argument(
        "json_file",
        metavar="patch.json",
        help="The patch envelope to apply (for example the spec_patch.json "
        "the authoring tool exports).",
    )
    _offer(
        node,
        "backend",
        help=f"{_BACKEND} A patched criteria tree is checked against the "
        "baselines this partition of the reference root holds for the test "
        f"(<reference_root>/<Backend>/<os>/). {_BACKEND_DEFAULT}",
    )

    node = tree.command(
        sub,
        "spec schema",
        summary="Print the schema of test_spec.json or testing.json",
        description="""
            Print a JSON-Schema of one of the project's two files, for an
            editor to check and complete it by: test_spec.json (every key
            of an entry, every metric and its fields) or testing.json
            (every key with its type, default and description, and each
            backend's own settings). It reads no project: the schema is
            this installation's, plug-ins included.
            """,
        examples=(
            "dstf spec schema",
            "dstf spec schema -o test_spec.schema.json",
            "dstf spec schema testing -o testing.schema.json",
        ),
    )
    node.add_argument(
        "schema",
        nargs="?",
        choices=list(SCHEMAS),
        default=next(iter(SCHEMAS)),
        help="Which file's schema: "
        + ", ".join(f"{name} ({file})" for name, file in SCHEMAS.items())
        + f". Default: {next(iter(SCHEMAS))}.",
    )
    _offer(
        node,
        "output",
        default=None,
        help="Write the schema to this file. Default: print it to standard output.",
    )


# ---------------------------------------------------------------------------
# baselines and the data beside them
# ---------------------------------------------------------------------------


def _model(node: argparse.ArgumentParser, what: str, *, optional: bool = False):
    node.add_argument(
        "model_id",
        metavar="model",
        **({"nargs": "?", "default": None} if optional else {}),
        help=what,
    )


def _add_baseline(tree: _Tree, top) -> None:
    group, sub = tree.group(
        top,
        "baseline",
        summary="Stored baselines: export them to one file",
        description="""
            Commands over the stored baselines of one partition of the
            reference root (<reference_root>/<Backend>/<os>/). Baselines
            are written by `dstf run --accept`, `dstf compare --accept`
            and the authoring tool's Accept.
            """,
        examples=("dstf baseline export --config testing.json -o references.json",),
    )
    node = tree.command(
        sub,
        "baseline export",
        summary="Write every stored baseline to one JSON or CSV file",
        description="""
            Write the stored baselines of the partition to one file: as
            JSON, every baseline whole (keyed by model id), or as CSV, one
            summary row per baseline. The reference root is not changed.
            """,
        examples=(
            "dstf baseline export --config testing.json -o references.json",
            "dstf baseline export --config testing.json --format csv --backend OpenModelica",
        ),
    )
    _offer(
        node,
        "format",
        default=next(iter(EXPORT_FILES)),
        help="The file's format: json (every baseline whole) or csv (one "
        f"summary row each). Default: {next(iter(EXPORT_FILES))}.",
    )
    _offer(
        node,
        "output",
        default=None,
        help="The file to write. Default: "
        + " or ".join(EXPORT_FILES.values())
        + ", by the format, in the work directory.",
    )
    _offer(node, "backend")
    _offer(
        node,
        "work_dir",
        fill={"work_dir_use": "Where the file is written when --output names none."},
    )


def _add_soft_check(tree: _Tree, top) -> None:
    group, sub = tree.group(
        top,
        "soft-check",
        summary="Advisory baselines from elsewhere: add, list, remove",
        description="""
            Soft checks are baselines imported from another regression
            system or another backend, kept beside a test's own baseline. A
            criterion that names one is scored against it as a warning: it
            is reported, and never fails the test.
            """,
        examples=(
            "dstf soft-check add MyLib.Examples.Rod openmodelica refs/OpenModelica/linux/ref_0003.json --config testing.json",
            "dstf soft-check list --config testing.json",
        ),
    )

    node = tree.command(
        sub,
        "soft-check add",
        summary="Store another system's baseline as a soft check",
        description="""
            Store a reference file as a soft check of a test, under a name
            the test's criteria can score against. The source is a
            reference JSON as this tool writes them (a ref_NNNN.json): its
            time points and variables are copied into the test's
            soft_checks directory, with where they came from. The test
            needs a stored baseline of its own first.
            """,
        examples=(
            "dstf soft-check add MyLib.Examples.Rod openmodelica refs/OpenModelica/linux/ref_0003.json --config testing.json",
        ),
        creates=True,
    )
    _model(node, "The test the soft check belongs to, by its model id.")
    node.add_argument(
        "name",
        help="The soft check's name: what a criterion's `against` names to "
        "be scored against it.",
    )
    node.add_argument(
        "source",
        help="The reference JSON to store: a ref_NNNN.json of another "
        "partition or another system (it needs `time` and `variables`).",
    )
    _offer(node, "backend")

    node = tree.command(
        sub,
        "soft-check list",
        summary="List a test's soft checks, or every test's",
        description="""
            List the soft checks stored in the partition: for one test, or
            for every test that has any. Each line gives the name, the
            number of variables and where it came from. Nothing is written.
            """,
        examples=(
            "dstf soft-check list --config testing.json",
            "dstf soft-check list MyLib.Examples.Rod --config testing.json",
        ),
    )
    _model(
        node,
        "List this test's soft checks only. Default: every test's.",
        optional=True,
    )
    _offer(node, "backend")

    node = tree.command(
        sub,
        "soft-check remove",
        summary="Remove a soft check from a test",
        description="""
            Remove one soft check of a test: its file is deleted from the
            partition. A criterion that still names it has no baseline left
            to be scored against.

            Exit code: 0, or 1 when the test has no soft check of that name.
            """,
        examples=(
            "dstf soft-check remove MyLib.Examples.Rod openmodelica --config testing.json",
        ),
    )
    _model(node, "The test the soft check belongs to, by its model id.")
    node.add_argument("name", help="The soft check to remove, by its name.")
    _offer(node, "backend")


def _add_companion(tree: _Tree, top) -> None:
    group, sub = tree.group(
        top,
        "companion",
        summary="Plot-only data beside a test: add, list, freeze, remove",
        description="""
            Companions are measured, analytical or other outside data drawn
            over a test's plots in the reports and the authoring tool. They
            are never scored: no criterion can name one.
            """,
        examples=(
            "dstf companion add MyLib.Examples.Rod rig data/rig.csv --config testing.json --map rod.T[1]=T_inlet",
            "dstf companion list --config testing.json",
        ),
    )

    node = tree.command(
        sub,
        "companion add",
        summary="Register a data file as a companion of a test",
        description="""
            Register a CSV or JSON file as a companion of a test. It writes
            a small record in the test's companions directory that points
            at the file where it is (`dstf companion freeze` copies the
            data in). The test needs a stored baseline first.

            --map says which of the file's variables is drawn on which of
            the test's; both names are checked here, against the baseline
            and against the file.
            """,
        examples=(
            "dstf companion add MyLib.Examples.Rod rig data/rig.csv --config testing.json",
            "dstf companion add MyLib.Examples.Rod rig data/rig.csv --config testing.json --map rod.T[1]=T_inlet --map rod.T[2]=T_outlet",
        ),
        creates=True,
    )
    _model(node, "The test the companion belongs to, by its model id.")
    node.add_argument(
        "name", help="The companion's name: its label in a plot's legend."
    )
    node.add_argument("path", help="The data file: a CSV or a JSON file.")
    _offer(
        node,
        "format",
        default=None,
        help="The file's format. Default: by the file's extension (.csv is "
        "csv, anything else json).",
    )
    node.add_argument(
        "--map",
        dest="variable_map",
        action="append",
        metavar="TEST_VAR=COMPANION_VAR",
        default=None,
        help="Draw the file's COMPANION_VAR on the plot of the test's "
        "TEST_VAR. REPEAT the flag, once per variable. Two test variables "
        "may name one companion variable (one curve, drawn twice); a test "
        "variable is named once. Default: none — a companion variable is "
        "then drawn where its name, or its expression, matches a test "
        "variable's.",
    )
    _offer(node, "backend")

    node = tree.command(
        sub,
        "companion list",
        summary="List a test's companions, or every test's",
        description="""
            List the companions registered in the partition: for one test,
            or for every test that has any. Each is shown with its kind
            (external or frozen), its format, its file and its variable
            mapping. Nothing is written.
            """,
        examples=(
            "dstf companion list --config testing.json",
            "dstf companion list MyLib.Examples.Rod --config testing.json",
        ),
    )
    _model(
        node,
        "List this test's companions only. Default: every test's.",
        optional=True,
    )
    _offer(node, "backend")

    node = tree.command(
        sub,
        "companion freeze",
        summary="Copy a companion's data into the reference storage",
        description="""
            Copy an external companion's data file into the test's
            companions directory, so the reference root holds it and the
            original may move or go. A companion that is already frozen is
            left as it is.

            Exit code: 0 (a companion already frozen too), or 1 when the
            test has no companion of that name or the file it points at is
            not there.
            """,
        examples=(
            "dstf companion freeze MyLib.Examples.Rod rig --config testing.json",
        ),
        creates=True,
    )
    _model(node, "The test the companion belongs to, by its model id.")
    node.add_argument("name", help="The companion to freeze, by its name.")
    _offer(node, "backend")

    node = tree.command(
        sub,
        "companion remove",
        summary="Remove a companion from a test",
        description="""
            Remove one companion of a test: its record, and its data when
            it was frozen, are deleted from the partition. An external
            file it pointed at is not touched.

            Exit code: 0, or 1 when the test has no companion of that name.
            """,
        examples=(
            "dstf companion remove MyLib.Examples.Rod rig --config testing.json",
        ),
    )
    _model(node, "The test the companion belongs to, by its model id.")
    node.add_argument("name", help="The companion to remove, by its name.")
    _offer(node, "backend")


def _add_manifest(tree: _Tree, top) -> None:
    group, sub = tree.group(
        top,
        "manifest",
        summary="The stored references by id: show, dump, clean up",
        description="""
            Every stored baseline is a ref_NNNN.json whose number is never
            reused; the model it belongs to is written inside it. These
            commands list that mapping and remove what is no longer used.
            """,
        examples=(
            "dstf manifest show --config testing.json",
            "dstf manifest cleanup --config testing.json --apply",
        ),
    )

    node = tree.command(
        sub,
        "manifest show",
        summary="List the stored references and the status of each",
        description="""
            List the references of the partition: each one's id, status
            and model, with the counts of active, skipped and obsolete
            ones. Nothing is written.
            """,
        examples=(
            "dstf manifest show --config testing.json",
            "dstf manifest show --config testing.json --show-obsolete --backend OpenModelica",
        ),
    )
    node.add_argument(
        "--show-obsolete",
        action="store_true",
        help="List the obsolete references too (they are only counted otherwise).",
    )
    _offer(node, "backend")

    node = tree.command(
        sub,
        "manifest dump",
        summary="Write the reference id to model id mapping as JSON",
        description="""
            Write reference_manifest.json to the work directory: the
            active references of the partition, ref_NNNN to model id. (A
            run writes the same file.)
            """,
        examples=("dstf manifest dump --config testing.json --work-dir out",),
    )
    _offer(node, "backend")
    _offer(
        node,
        "work_dir",
        fill={"work_dir_use": "Where reference_manifest.json is written."},
    )

    node = tree.command(
        sub,
        "manifest cleanup",
        summary="Remove obsolete references, or a run's leftovers",
        description="""
            List what is no longer used, and with --apply remove it. By
            default that is the partition's reference files marked
            obsolete. With --orphans it is, in the work directory, the
            entries, result directories and report pages of models the
            project no longer declares.

            Nothing is removed without --apply: the command lists what it
            would remove and exits 0.
            """,
        examples=(
            "dstf manifest cleanup --config testing.json",
            "dstf manifest cleanup --config testing.json --apply",
            "dstf manifest cleanup --config testing.json --orphans --apply",
        ),
    )
    node.add_argument(
        "--orphans",
        action="store_true",
        help="Clean the work directory instead of the reference root: the "
        "last runs' entries and directories of models the project no longer "
        "declares.",
    )
    node.add_argument(
        "--apply",
        action="store_true",
        help="Remove what is listed. Without it nothing is removed.",
    )
    _offer(node, "backend")
    _offer(
        node,
        "work_dir",
        fill={"work_dir_use": "The work directory --orphans cleans."},
    )


# ---------------------------------------------------------------------------
# this machine
# ---------------------------------------------------------------------------


def _add_check(tree: _Tree, top) -> None:
    node = tree.command(
        top,
        "check",
        summary="Find the backends installed here; diagnose one",
        description="""
            Say where each backend is on this machine and by which rule it
            was found, then diagnose one of them part by part: the entry or
            backend named, or the one the project runs with. Nothing is simulated
            and nothing is written; with no project here, the search alone
            is reported.

            Exit code: 0 when the diagnosed backend is ready, else 1 with
            what to fix.
            """,
        examples=(
            "dstf check",
            "dstf check --config testing.json",
            "dstf check OpenModelica",
            "dstf check 'Dymola 2025'",
        ),
    )
    node.add_argument(
        # Not "backend": that is `--backend`'s name, which names the backend
        # a project RUNS with (`_build_config`).
        "check_backend",
        nargs="?",
        default=None,
        choices=_BackendNames(),
        metavar="backend",
        help="Diagnose this one alone: an entry of testing.json's backends, by "
        f"its name, or a backend — {_BACKENDS}. A backend's name diagnoses "
        "its entry the project runs with, else its first. Default: list "
        "every entry and every other backend, then diagnose the one the "
        "project runs with.",
    )


__all__ = [
    "COMMAND_GROUPS",
    "PROJECT_FLAGS",
    "READS",
    "build_arg_parser",
    "command_path",
]
