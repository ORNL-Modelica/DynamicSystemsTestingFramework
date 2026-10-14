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
* ``[--flag VALUE]`` around an optional part, as a synopsis writes it;
* a trailing ``# comment``, a ``\\`` continuation, and what follows a pipe
  or a redirection.

A value the parser checks — a number, one of a set of choices — must be a
real one.
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


def argv_of(command: str) -> list[str]:
    """The arguments ``command`` (which starts with ``dstf``) passes: its
    comment and the shell's words cut, a synopsis's ``[…]`` opened."""
    words = shlex.split(command, comments=True)[1:]
    argv: list[str] = []
    depth = 0
    for word in words:
        if word in _SHELL:
            break
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


def test_the_pages_are_read():
    """The fence is only as good as what it finds: the pages that teach the
    command line each show commands, and they are found."""
    counts = {
        page.relative_to(REPO).as_posix(): sum(1 for s in SHOWN if s.page == page)
        for page in PAGES
    }
    assert counts["README.md"] >= 1, counts
    assert counts["CLAUDE.md"] >= 5, counts
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
    assert argv_of("dstf soft-check list [<model>]") == [
        "soft-check",
        "list",
        "<model>",
    ]
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
