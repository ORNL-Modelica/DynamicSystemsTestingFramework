"""Every `dstf …` command line the documentation shows in a code block
parses through the real command tree.

A command renamed, a flag moved to another action or dropped: the page that
still shows the old line fails here, with its file and line. Read:
``README.md``, ``CHANGELOG.md``, ``CLAUDE.md``, ``docs/releasing.md``, every
page of the documentation site (each ``.md`` under ``docs/`` that
``docs/conf.py`` does not exclude) and every ``README.md`` under
``examples/``.

What a page may write and still parse:

* ``uv run dstf …`` / ``python -m dstf …`` / a ``$`` prompt before the command;
* ``<placeholder>`` wherever a value is a free string (a model, a path);
* ``[--flag VALUE]`` around an optional part, as a synopsis writes it, and
  ``[<placeholder>]`` for an optional positional, which is left out;
* a trailing ``# comment``, a ``\\`` continuation, and what follows a pipe
  or a redirection.

A value the parser checks — a number, one of a set of choices — must be a
real one.

Prose shows commands too, as inline code that starts with ``dstf ``
(:func:`spans_in`); each is a command line, with three narrow rules and
the exceptions in :data:`EXCEPTIONS`:

* a span that names its command by a placeholder (``dstf <command>
  --help``) is a fragment of a sentence, and is skipped;
* a span of a command's words alone (``dstf spec update``) names the
  command: those words must be a command or a group of the tree;
* the first column of a table written after :data:`HISTORY` is history —
  CHANGELOG.md's 1.0 names beside their 1.1 ones — and is not read.
"""

from __future__ import annotations

import contextlib
import fnmatch
import io
import re
import runpy
import shlex
from dataclasses import dataclass
from pathlib import Path

import pytest

from dstf.cli import build_arg_parser

REPO = Path(__file__).resolve().parents[1]

#: Downloaded by ``scripts/fetch_reference_fmus.py`` where it was run: another
#: project's pages, not ours.
_FETCHED = REPO / "examples" / "fmu" / "reference-fmus-binaries"

DOCS = REPO / "docs"


def site_pages() -> list[Path]:
    """The pages of the documentation site: every ``.md`` under ``docs/``
    that the build does not leave out (``exclude_patterns`` in
    ``docs/conf.py``, matched as Sphinx matches them: by the page's path
    under ``docs/``, or by a folder it is in)."""
    excluded = runpy.run_path(str(DOCS / "conf.py"))["exclude_patterns"]
    pages = []
    for page in sorted(DOCS.rglob("*.md")):
        rel = page.relative_to(DOCS).as_posix()
        parts = rel.split("/")
        folders = ["/".join(parts[:i]) for i in range(1, len(parts))]
        if any(
            fnmatch.fnmatch(rel, pattern) or pattern in folders for pattern in excluded
        ):
            continue
        pages.append(page)
    return pages


PAGES = [
    REPO / "README.md",
    REPO / "CHANGELOG.md",
    REPO / "CLAUDE.md",
    REPO / "docs" / "releasing.md",
    *site_pages(),
    *sorted(
        page
        for page in (REPO / "examples").rglob("README.md")
        if _FETCHED not in page.parents
    ),
]

_FENCE = re.compile(r"^\s*(```|~~~)")
#: What may stand before the command on its line.
_LEAD = re.compile(
    r"^(?:\$\s+)?(?:uv run (?:--\S+ )*)?(?:python3? -m )?(?=dstf(?:\s|$))"
)
#: Where the command ends and the shell's own words begin.
_SHELL = {"|", ">", ">>", "<", "&&", "||", ";", "2>", "2>&1", "&"}


@dataclass(frozen=True)
class Shown:
    """One command line a page shows."""

    page: Path
    line: int
    text: str

    @property
    def where(self) -> str:
        return f"{self.page.relative_to(REPO).as_posix()}:{self.line}"


def commands_in(text: str, page: Path) -> list[Shown]:
    """The `dstf` command lines inside the fenced code blocks of ``text``:
    a line that is output, a comment or another program's is skipped, a
    ``\\`` continuation joined."""
    found: list[Shown] = []
    fenced = False
    pending: tuple[int, str] | None = None
    for number, raw in enumerate(text.splitlines(), start=1):
        if _FENCE.match(raw):
            fenced, pending = not fenced, None
            continue
        if not fenced:
            continue
        line = raw.strip()
        if pending is not None:
            start, so_far = pending
            line = f"{so_far} {line}"
        else:
            start = number
            lead = _LEAD.match(line)
            if lead is None:
                continue
            line = line[lead.end() :]
        if line.endswith("\\"):
            pending = (start, line[:-1].rstrip())
            continue
        pending = None
        found.append(Shown(page, start, line))
    return found


#: An inline code span of prose that is a command line.
_SPAN = re.compile(r"(?<!`)`(dstf [^`]+)`(?!`)")
#: Written on its own line before a table whose FIRST column is history:
#: the commands of an earlier version, which today's tree is not asked to
#: parse (CHANGELOG.md's table of 1.0 names beside their 1.1 ones).
HISTORY = "<!-- commands: the first column is history -->"
#: A span that names its command by a placeholder: a fragment.
_FRAGMENT = re.compile(r"dstf <[\w-]+>")
#: The spans a page may show that the tree here cannot parse, by page and
#: text, each with why.
EXCEPTIONS = {
    ("docs/guides/plugin.md", "dstf check Decay"): (
        "the guide's plug-in registers the Decay backend; this test loads no plug-in"
    ),
}


def spans_in(text: str, page: Path) -> list[Shown]:
    """The inline code spans of ``text`` outside its fenced blocks that
    start with ``dstf ``, but those in a :data:`HISTORY` table's first
    column."""
    found: list[Shown] = []
    fenced = False
    history = ""  # "", "waiting" for the table, or "in" it
    for number, raw in enumerate(text.splitlines(), start=1):
        if _FENCE.match(raw):
            fenced = not fenced
            continue
        if fenced:
            continue
        line = raw.strip()
        if line == HISTORY:
            history = "waiting"
            continue
        if history and line.startswith("|"):
            history = "in"
            line = "|".join(line.split("|")[2:])
        elif history == "in":
            history = ""
        found.extend(Shown(page, number, m.group(1)) for m in _SPAN.finditer(line))
    return found


def argv_of(command: str) -> list[str]:
    """The arguments ``command`` (which starts with ``dstf``) passes: its
    comment and the shell's words cut, a synopsis's ``[…]`` opened."""
    words = shlex.split(command, comments=True)[1:]
    argv: list[str] = []
    depth = 0
    for word in words:
        if word in _SHELL:
            break
        # `[<model>]`: an optional positional, left out — a placeholder can
        # stand for no value the parser checks (a backend's name).
        if re.fullmatch(r"\[<[^>]+>\]", word):
            continue
        # `[--port 8090]`, `[<model>]`: optional in a synopsis. A bracket
        # inside a value (`Test[A,B]*`, `T[1]`) is the value's own.
        if re.match(r"\[(--?\w|<)", word):
            depth += 1
            word = word[1:]
        if depth and word.endswith("]") and word.count("[") < word.count("]"):
            depth -= 1
            word = word[:-1]
        argv.append(word)
    return argv


def refusal(command: str) -> str | None:
    """Why the tree refuses ``command`` — its last line of usage error — or
    None when it parses. ``--help`` and ``--version`` exit 0: they parse."""
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            build_arg_parser().parse_args(argv_of(command))
    except SystemExit as exc:
        if exc.code not in (0, None):
            return err.getvalue().strip().splitlines()[-1]
    return None


SHOWN = [
    shown
    for page in PAGES
    for shown in commands_in(page.read_text(encoding="utf-8"), page)
]

SPANS = [
    shown
    for page in PAGES
    for shown in spans_in(page.read_text(encoding="utf-8"), page)
]

#: Every command and group of the tree, by the words it is typed with.
_NODES = set(build_arg_parser().nodes)


def span_refusal(shown: Shown) -> str | None:
    """Why the tree refuses the command line an inline span shows, or None
    (its rules: the module's docstring)."""
    page = shown.page.relative_to(REPO).as_posix()
    if (page, shown.text) in EXCEPTIONS or _FRAGMENT.match(shown.text):
        return None
    words = argv_of(shown.text)
    if words and " ".join(words) in _NODES:
        return None
    return refusal(shown.text)


def test_every_command_line_the_pages_show_parses():
    failures = [
        f"{shown.where}: {shown.text}\n      {why}"
        for shown in SHOWN
        for why in [refusal(shown.text)]
        if why is not None
    ]
    assert not failures, (
        f"{len(failures)} command line(s) in the documentation do not parse:\n  "
        + "\n  ".join(failures)
    )


def test_every_command_line_the_prose_shows_parses():
    failures = [
        f"{shown.where}: `{shown.text}`\n      {why}"
        for shown in SPANS
        for why in [span_refusal(shown)]
        if why is not None
    ]
    assert not failures, (
        f"{len(failures)} inline command(s) in the documentation do not parse:\n  "
        + "\n  ".join(failures)
    )


def test_the_prose_is_read():
    """The spans are found where the pages show them: the guides' prose,
    the CHANGELOG's table (its 1.1 column only), and each exception is one a
    page still shows."""
    where = {(s.page.relative_to(REPO).as_posix(), s.text) for s in SPANS}
    assert len(SPANS) >= 40, len(SPANS)
    assert ("docs/guides/ci.md", "dstf check") in where
    assert ("CHANGELOG.md", "dstf baseline export") in where
    assert ("CHANGELOG.md", "dstf export") not in where, "the 1.0 column was read"
    assert set(EXCEPTIONS) <= where


def test_the_pages_are_read():
    """The fence is only as good as what it finds: the pages that teach the
    command line each show commands, and they are found."""
    counts = {
        page.relative_to(REPO).as_posix(): sum(1 for s in SHOWN if s.page == page)
        for page in PAGES
    }
    assert counts["README.md"] >= 1, counts
    assert counts["CLAUDE.md"] >= 3, counts
    assert counts["examples/python/PythonTestingLib/README.md"] >= 1, counts
    assert set(counts) >= {"docs/releasing.md", "examples/fmu/README.md"}
    # The guides teach the command line: each shows commands, and so many
    # in all that a page silently skipped would be missed.
    guides = {page: n for page, n in counts.items() if page.startswith("docs/guides/")}
    assert len(guides) >= 14, guides
    assert sum(guides.values()) >= 20, guides
    for page in ("first-run", "testing-json", "add-a-test", "baselines", "ci"):
        assert guides[f"docs/guides/{page}.md"] >= 1, guides


def test_the_site_s_pages_are_the_built_ones():
    """The site's pages are found by the build's own exclusions: a guide is
    read, a contributor note is not."""
    pages = {page.relative_to(REPO).as_posix() for page in site_pages()}
    assert {"docs/index.md", "docs/concepts.md", "docs/guides/first-run.md"} <= pages
    assert "docs/reference/cli.md" in pages
    assert not {"docs/decisions.md", "docs/ideas.md", "docs/architecture.md"} & pages
    assert not any(page.startswith("docs/superpowers/") for page in pages)


# --- the reader itself -------------------------------------------------------

_PAGE = """\
Prose with `dstf nope` in it is not a command line.

```bash
# a comment, then commands
uv run dstf --config testing.json run --report   # trailing words
$ dstf discover
python -m dstf compare --report-format junit \\
    -o results.xml
dstf spec schema testing > testing.schema.json
uv sync --extra dev
Total: 13 tests
dstfx run
```

    dstf indented-not-fenced

~~~
dstf companion add <model> <name> <path> [--map TEST_VAR=COMPANION_VAR]
dstf author --config testing.json [--port 8090] [--no-browser]
dstf run --filter "Test[A,B]*"
~~~
"""


def test_the_reader_finds_the_commands_and_only_them():
    found = commands_in(_PAGE, REPO / "README.md")
    assert [(s.line, s.text) for s in found] == [
        (5, "dstf --config testing.json run --report   # trailing words"),
        (6, "dstf discover"),
        (7, "dstf compare --report-format junit -o results.xml"),
        (9, "dstf spec schema testing > testing.schema.json"),
        (18, "dstf companion add <model> <name> <path> [--map TEST_VAR=COMPANION_VAR]"),
        (19, "dstf author --config testing.json [--port 8090] [--no-browser]"),
        (20, 'dstf run --filter "Test[A,B]*"'),
    ]
    assert found[0].where == "README.md:5"
    assert [refusal(s.text) for s in found] == [None] * 7


def test_the_reader_opens_a_synopsis_and_leaves_a_value_alone():
    assert argv_of("dstf author [--port 8090] [--no-browser]") == [
        "author",
        "--port",
        "8090",
        "--no-browser",
    ]
    assert argv_of("dstf soft-check list [<model>]") == ["soft-check", "list"]
    assert argv_of("dstf check [<backend>] --config c") == ["check", "--config", "c"]
    assert argv_of('dstf run --filter "Test[A,B]*" | tee log') == [
        "run",
        "--filter",
        "Test[A,B]*",
    ]
    assert argv_of("dstf spec add M --variables 'T[1]' # why") == [
        "spec",
        "add",
        "M",
        "--variables",
        "T[1]",
    ]


_PROSE = """\
Run `dstf --config testing.json run` first; `dstf spec update` names a
command, `dstf <command> --help` a fragment, ``dstf nope`` is no span.

<!-- commands: the first column is history -->

| 1.0 | 1.1 |
|---|---|
| `dstf export` | `dstf baseline export` |

After the table `dstf companion freeze` is read again.

```console
$ dstf discover   # fenced: commands_in reads it, spans_in does not
```
"""


def test_the_span_reader_finds_the_prose_s_commands_and_only_them():
    found = spans_in(_PROSE, REPO / "README.md")
    assert [(s.line, s.text) for s in found] == [
        (1, "dstf --config testing.json run"),
        (1, "dstf spec update"),
        (2, "dstf <command> --help"),
        (8, "dstf baseline export"),
        (10, "dstf companion freeze"),
    ]
    assert [span_refusal(s) for s in found] == [None] * 5


@pytest.mark.parametrize(
    ("span", "why"),
    [
        # a command renamed, in prose and in a table's 1.1 column
        ("dstf chek", "invalid choice: 'chek'"),
        ("dstf baseline exprt", "invalid choice: 'exprt'"),
        ("dstf spec-update", "invalid choice: 'spec-update'"),
        ("dstf run --rep", "unrecognized arguments: --rep"),
    ],
)
def test_a_span_that_does_not_parse_is_refused(span, why):
    shown = Shown(REPO / "docs" / "guides" / "ci.md", 1, span)
    assert why in (span_refusal(shown) or "")


@pytest.mark.parametrize(
    ("command", "why"),
    [
        ("dstf export-schema", "invalid choice: 'export-schema'"),
        ("dstf manifest show --orphans", "unrecognized arguments: --orphans"),
        ("dstf run --timeout 0", "argument --timeout: 0: must be"),
        ("dstf --test-spec s.json author", "--test-spec does not apply"),
        ("dstf companion add <model>", "the following arguments are required"),
    ],
)
def test_a_line_that_does_not_parse_is_refused_in_the_parser_s_words(command, why):
    assert why in (refusal(command) or "")
