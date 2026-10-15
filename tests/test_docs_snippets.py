"""Every JSON snippet the documentation shows is read by the loader of the
file it is a snippet of.

A page that shows ``testing.json`` or ``test_spec.json`` teaches by
example, and an example that the tool refuses teaches the wrong thing: a
key renamed or removed, a value of another type, and the page that still
shows it fails here, with its file and line. Read: the pages of the
documentation site (``test_docs_commands.site_pages``), ``README.md`` and
``CHANGELOG.md``.

Each ```` ```json ```` block is led by a marker on the line before it, which
says what the snippet is:

* ``<!-- snippet: testing -->`` — a whole ``testing.json``: the strict check
  of its keys, and of each ``backends`` block against its backend's table;
* ``<!-- snippet: testing-refused -->`` — a ``testing.json`` the tool refuses
  (the old shape a changelog shows);
* ``<!-- snippet: test_spec -->`` — a whole ``test_spec.json``: the spec
  parser, which refuses a key nothing reads;
* ``<!-- snippet: criterion -->`` — one node of a ``metrics`` tree, read as
  the tree of a test;
* ``<!-- snippet: fragment -->`` — a part of a file that is not read on its
  own. Every fragment is listed in :data:`FRAGMENTS`, so one is a decision.

A ``json`` block with no marker fails.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from dstf.config import ConfigError, _backend_blocks, load_config_file
from dstf.discovery.spec_parser import (
    spec_document_from_bytes,
    spec_models_from_document,
)
from test_docs_commands import REPO, site_pages

PAGES = [REPO / "README.md", REPO / "CHANGELOG.md", *site_pages()]

_MARK = re.compile(r"^<!-- snippet: ([a-z_-]+) -->$")
_OPEN = re.compile(r"^\s*```json\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
KINDS = {"testing", "testing-refused", "test_spec", "criterion", "fragment"}

#: The snippets that are parts of a file, by page and the text's first line.
FRAGMENTS: set[tuple[str, str]] = set()


@dataclass(frozen=True)
class Snippet:
    page: Path
    line: int
    kind: str | None
    text: str

    @property
    def where(self) -> str:
        return f"{self.page.relative_to(REPO).as_posix()}:{self.line}"


def snippets_in(text: str, page: Path) -> list[Snippet]:
    """The ``json`` code blocks of ``text``, each with the marker on the
    line before its fence (None when there is none)."""
    found: list[Snippet] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if _OPEN.match(lines[i]):
            before = lines[i - 1].strip() if i else ""
            mark = _MARK.match(before)
            body: list[str] = []
            j = i + 1
            while j < len(lines) and not _FENCE.match(lines[j]):
                body.append(lines[j])
                j += 1
            found.append(
                Snippet(page, i + 1, mark.group(1) if mark else None, "\n".join(body))
            )
            i = j + 1
            continue
        if _FENCE.match(lines[i]):  # another language's block: skip it whole
            j = i + 1
            while j < len(lines) and not _FENCE.match(lines[j]):
                j += 1
            i = j + 1
            continue
        i += 1
    return found


SNIPPETS = [s for page in PAGES for s in snippets_in(page.read_text("utf-8"), page)]


def read_testing(text: str, tmp_path: Path) -> None:
    """``text`` as ``testing.json`` is read: its keys and their types, then
    each backend's block by that backend's table. Raises ConfigError."""
    path = tmp_path / "testing.json"
    path.write_text(text, encoding="utf-8")
    data = load_config_file(path)
    if "backends" in data:
        _backend_blocks(data["backends"], str(path), tmp_path)


def read_spec(text: str, tmp_path: Path, label: str) -> list:
    """``text`` as ``test_spec.json`` is read. Raises ConfigError."""
    document = spec_document_from_bytes(text.encode("utf-8"), label)
    return spec_models_from_document(document, base_dir=tmp_path, label=label)


def as_spec(node_text: str) -> str:
    """One node of a criteria tree, as the tree of a test."""
    node = json.loads(node_text)
    return json.dumps(
        {"tests": [{"model": "Docs.Snippet", "variables": [], "metrics": node}]}
    )


def test_every_json_block_says_what_it_is():
    unmarked = [s.where for s in SNIPPETS if s.kind is None]
    unknown = [f"{s.where}: {s.kind}" for s in SNIPPETS if s.kind not in KINDS | {None}]
    assert not unmarked, f"json blocks with no snippet marker: {unmarked}"
    assert not unknown, f"unknown snippet kinds: {unknown}"


def test_the_fragments_are_the_listed_ones():
    shown = {
        (s.page.relative_to(REPO).as_posix(), s.text.strip().splitlines()[0])
        for s in SNIPPETS
        if s.kind == "fragment"
    }
    assert shown == FRAGMENTS


@pytest.mark.parametrize(
    "snippet",
    [s for s in SNIPPETS if s.kind in {"testing", "test_spec", "criterion"}],
    ids=lambda s: s.where,
)
def test_a_snippet_is_read_by_its_file_s_loader(snippet, tmp_path):
    if snippet.kind == "testing":
        read_testing(snippet.text, tmp_path)
    elif snippet.kind == "test_spec":
        tests = read_spec(snippet.text, tmp_path, snippet.where)
        assert len(tests) == len(json.loads(snippet.text)["tests"])
    else:
        (test,) = read_spec(as_spec(snippet.text), tmp_path, snippet.where)
        assert test.metric_tree_spec is not None


@pytest.mark.parametrize(
    "snippet",
    [s for s in SNIPPETS if s.kind == "testing-refused"],
    ids=lambda s: s.where,
)
def test_a_refused_snippet_is_refused(snippet, tmp_path):
    with pytest.raises(ConfigError):
        read_testing(snippet.text, tmp_path)


def test_the_pages_hold_snippets_of_each_kind():
    """The fence is only as good as what it finds."""
    kinds = {s.kind for s in SNIPPETS}
    assert {"testing", "test_spec", "criterion", "testing-refused"} <= kinds
    assert sum(1 for s in SNIPPETS if s.kind == "testing") >= 6
    assert sum(1 for s in SNIPPETS if s.kind == "test_spec") >= 5


# --- the checks themselves ---------------------------------------------------


def test_a_key_nothing_reads_is_refused_in_each_kind(tmp_path):
    with pytest.raises(ConfigError, match="tolerence"):
        read_testing('{"tolerence": 0.01}', tmp_path)
    with pytest.raises(ConfigError, match="show_ide"):
        read_testing('{"backends": {"OpenModelica": {"show_ide": true}}}', tmp_path)
    with pytest.raises(ConfigError, match="max_val"):
        read_spec(
            as_spec('{"metric": "range", "variable": "x", "max_val": 1}'),
            tmp_path,
            "t",
        )
    with pytest.raises(ConfigError, match="stop_tme"):
        read_spec(
            '{"tests": [{"model": "M", "simulation": {"stop_tme": 1}}]}', tmp_path, "t"
        )


def test_the_reader_takes_the_marker_on_the_line_before_the_fence():
    page = REPO / "README.md"
    text = (
        "<!-- snippet: testing -->\n```json\n{}\n```\n\n"
        "```bash\ndstf run\n```\n"
        "```json\n[1]\n```\n"
    )
    found = snippets_in(text, page)
    assert [(s.line, s.kind, s.text) for s in found] == [
        (2, "testing", "{}"),
        (9, None, "[1]"),
    ]
