"""``dstf summary``'s output: the facts of :mod:`dstf.reporting.results_summary`
as Markdown a reviewer pastes into a pull request (:func:`as_markdown`), or
as JSON (:func:`as_json`).

The Markdown: a matrix — a row per backend/os, a column per label, each
cell PASS over the run's total, then in one parenthesis the other status
words' counts and, with a base and a test not passing, ``K also fail on
<base>`` — under it one key line, then one folded list per cell with tests
that did not pass, its summary line ``<Backend>/<os>, <label>: N not
passing, K also fail on <base>``. A list is open when it holds a test that is new (one the
base did not fail and did not end the same way — ``NotPassing.not_new``),
or there is no base to tell; folded otherwise; the base's own lists are
folded. New ones are listed first.
"""

from __future__ import annotations

import html
import json
from dataclasses import asdict

from .results_summary import Cell, Summary
from .status import PASS

#: A cell of a label that has no run of that backend/os.
_DASH = "–"


def _row(cells: list[str]) -> str:
    """A table row; a ``|`` in a cell is escaped, so it keeps the columns."""
    return "| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |"


def _also_fail(n: int, base: str | None) -> str:
    return f"{n} also {'fails' if n == 1 else 'fail'} on {base}"


def _matrix_cell(summary: Summary, cell: Cell | None) -> str:
    if cell is None:
        return _DASH
    others = ", ".join(
        f"{n} {word}"
        for word, n in cell.totals.items()
        if word not in ("total", PASS) and n
    )
    if cell.not_new is not None and cell.not_passing:
        also = _also_fail(cell.not_new, summary.base)
        others = f"{others}; {also}" if others else also
    text = f"{cell.passed}/{cell.total}"
    return f"{text} ({others})" if others else text


def _open(summary: Summary, cell: Cell) -> bool:
    if summary.base is None or cell.not_new is None:
        return cell.label != summary.base
    return len(cell.not_passing) > cell.not_new


def _summary_line(summary: Summary, cell: Cell) -> str:
    text = f"{cell.folder}, {cell.label}: {len(cell.not_passing)} not passing"
    if cell.not_new is not None:
        text += f", {_also_fail(cell.not_new, summary.base)}"
    elif cell.base_note:
        text += f"; {cell.base_note}"
    return text


def _test(model_id: str) -> str:
    short = model_id.rsplit(".", 1)[-1]
    return f'<code title="{html.escape(model_id)}">{html.escape(short)}</code>'


def _list(summary: Summary, cell: Cell) -> list[str]:
    with_base = cell.not_new is not None
    header = [
        "Test",
        "Status",
        *([str(summary.base)] if with_base else []),
        "What failed",
    ]
    rows = sorted(cell.not_passing, key=lambda r: r.not_new)
    lines = [
        "<details open>" if _open(summary, cell) else "<details>",
        f"<summary>{html.escape(_summary_line(summary, cell))}</summary>",
        "",
        _row(header),
        _row(["---"] * len(header)),
    ]
    for r in rows:
        base = [str(r.base)] if with_base else []
        lines.append(
            _row(
                [_test(r.model_id), r.status, *base, html.escape(r.reason, quote=False)]
            )
        )
    return [*lines, "", "</details>"]


def as_markdown(summary: Summary) -> str:
    """The Markdown a reviewer pastes into a pull request."""
    lines = [_row(["Tool / OS", *summary.labels])]
    lines.append(_row(["---"] * (len(summary.labels) + 1)))
    for folder in summary.folders:
        cells = [
            _matrix_cell(summary, summary.cells.get((label, folder)))
            for label in summary.labels
        ]
        lines.append(_row([folder, *cells]))
    key = "Passed / run."
    if summary.base is not None:
        key += (
            f' "also fails on {summary.base}" = the test fails on {summary.base} '
            "too (or ends there the same way)."
        )
    lines += ["", key, ""]
    for folder in summary.folders:
        for label in summary.labels:
            cell = summary.cells.get((label, folder))
            if cell is not None and cell.not_passing:
                lines += [*_list(summary, cell), ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def as_json(summary: Summary) -> str:
    """The facts as JSON: every cell, by backend/os then label, with its
    tests that did not pass."""
    cells = []
    for folder in summary.folders:
        for label in summary.labels:
            cell = summary.cells.get((label, folder))
            if cell is None:
                continue
            backend, _, os_name = folder.rpartition("/")
            cells.append(
                {
                    "label": label,
                    "folder": folder,
                    "backend": backend,
                    "os": os_name,
                    "file": str(cell.path),
                    "passed": cell.passed,
                    "total": cell.total,
                    "totals": cell.totals,
                    "not_new": cell.not_new,
                    "base_note": cell.base_note,
                    "not_passing": [asdict(r) for r in cell.not_passing],
                }
            )
    data = {
        "labels": summary.labels,
        "base": summary.base,
        "folders": summary.folders,
        "cells": cells,
    }
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
