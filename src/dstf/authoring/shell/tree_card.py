"""The criteria tree card (brief §3.2; the 2026-09-20 board, 1A / 1B):
closed it is one line — the root's verdict, what the root is, the tally,
and the first failures by name, each a jump to its criterion; open it is a
row per node with that node's controls, which are whatever capabilities
attach at the NODE placement. The card knows no capability by name.

A refresh rebuilds the rows only when the tree's SHAPE moved — the paths,
kinds and variables of its nodes. A verdict that flips, a readout, a
combinator's own count: those repaint into the row that is already there.
A cluster is elements enough that re-sending every row on every Session
event was the cost the 3b review measured (40 leaves: ~1150 elements per
sync with the tree open)."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from nicegui import ui

from ..capabilities import Placement
from ..session import Selection
from .actions import InlineHost, RowTarget
from .widgets import Pill

if TYPE_CHECKING:
    from .test_page import TestPage

NAMED = 2  #: failures the closed line names; the rest are counted


def leaves_of(view: dict | None) -> list[dict]:
    """The leaf nodes of a `tree_view`, in tree order."""
    if view is None:
        return []
    if view.get("kind") == "leaf":
        return [view]
    return [leaf for child in view.get("children") or [] for leaf in leaves_of(child)]


def nodes_of(view: dict | None) -> list[dict]:
    """Every node of a `tree_view`, combinators included, in tree order."""
    if view is None:
        return []
    return [view] + [
        node for child in view.get("children") or [] for node in nodes_of(child)
    ]


def shape_of(view: dict | None) -> tuple[tuple[str, str, str], ...]:
    """What a row is drawn from and cannot be repainted through. The paths
    are what decides the rows — one per node, painted by its path — and the
    kind and the leaf's variable ride along harmlessly: a row is built for
    that node's selection and controls, so a row whose kind or variable
    moved is one worth rebuilding anyway. The metric is NOT in it: a mode
    switch repaints the row's name in place."""
    return tuple(
        (str(node.get("path")), str(node.get("kind")), str(node.get("variable")))
        for node in nodes_of(view)
    )


def leaf_status(leaf: dict) -> str:
    """One of "pass", "fail", "warn", "queued": an advisory failure is the
    warn one — a soft check is not a regression."""
    passed = leaf.get("passed")
    if passed is True:
        return "pass"
    if passed is False:
        return "warn" if leaf.get("advisory") else "fail"
    return "queued"


def root_status(node: dict) -> str:
    """A combinator's own verdict, the evaluator's — never the tally of its
    leaves: an `or` passes on one child, a `warn` on none."""
    passed = node.get("passed")
    return "pass" if passed is True else "fail" if passed is False else "queued"


def node_status(node: dict) -> str:
    return leaf_status(node) if node.get("kind") == "leaf" else root_status(node)


def leaf_name(leaf: dict) -> str:
    """``metric · label`` — what a criterion's row READS. The label is the
    expression the model wrote for the variable (`nrmse · pipe.T[1]`), which
    the tree view carries beside the `variable` a patch op targets; a
    variable with no expression reads as its name, as it always did."""
    return f"{leaf.get('metric')} · {leaf.get('label') or leaf.get('variable')}"


def leaf_readout(leaf: dict) -> str:
    """The CLI's own words for the score: the criterion the leaf was held
    to — which ALREADY carries the score ("NRMSE 1.531e-01 vs tolerance
    1.000e-02 → FAIL"), so the display beside it said the number twice (the
    first screenshot) — else the display alone. A sentinel's criterion says
    the reason itself ("scalar leaf not scored — …"), so the error is not a
    branch of its own. The trailing verdict is not stripped: `tube` and
    `range` do not end with it ("… → FAIL (requires 0)"), so a strip would
    read the string wrong for half the modes."""
    return str(leaf.get("criterion") or leaf.get("score_display") or "")


def root_text(node: dict) -> str:
    """What the root IS, in the closed line. A staged root leaf is the
    test's one criterion — its metric and variable are already the line's
    only jump — and a combinator names itself and counts the LEAVES under
    it, however deep: `and[k-of-n[l, l], l]` is 3 criteria, not 2."""
    return "1 criterion" if node.get("kind") == "leaf" else node_text(node)


def node_text(node: dict) -> str:
    if node.get("kind") == "leaf":
        return leaf_name(node)
    n = len(leaves_of(node))
    text = f"{node.get('combinator')} · {n} {'criterion' if n == 1 else 'criteria'}"
    if node.get("combinator") == "k-of-n" and node.get("k") is not None:
        text += f" · k={node['k']}"
    return text


def summary_text(view: dict) -> tuple[str, list[dict], int]:
    """The closed line's three parts: the tally ("0 pass · 2 fail", plus an
    advisory and an unscored count when either is non-zero), the failing
    leaves the line NAMES — hard failures before advisory ones, at most
    `NAMED` of them — and how many failures are left over for "+k more".
    Board 1A draws each failure's name once, and it is the thing that
    jumps, so the tally does not repeat the names."""
    leaves = leaves_of(view)
    statuses = [leaf_status(leaf) for leaf in leaves]
    hard = [leaf for leaf, st in zip(leaves, statuses) if st == "fail"]
    advisory = [leaf for leaf, st in zip(leaves, statuses) if st == "warn"]
    parts = [f"{statuses.count('pass')} pass"]
    if hard:
        parts.append(f"{len(hard)} fail")
    if advisory:
        parts.append(f"{len(advisory)} advisory")
    if statuses.count("queued"):
        parts.append(f"{statuses.count('queued')} unscored")
    failed = hard + advisory
    return " · ".join(parts), failed[:NAMED], max(len(failed) - NAMED, 0)


class _Row:
    """One node's row, and what a repaint writes into it."""

    def __init__(self, pill: Pill, text: ui.label, readout: ui.label | None) -> None:
        self.pill, self.text, self.readout = pill, text, readout

    def paint(self, node: dict) -> None:
        self.pill.set(node_status(node))
        self.text.set_text(node_text(node))
        if self.readout is not None:
            self.readout.set_text(leaf_readout(node))


class TreeCard:
    def __init__(self, page: TestPage) -> None:
        self.page = page
        self.is_open = False
        self.named: list[dict] = []
        self.root_pill: Pill | None = None
        self.hosts: list[InlineHost] = []
        self.painters: dict[str, _Row] = {}
        self._shape: tuple[tuple[str, str, str], ...] = ()
        with ui.card().classes("dstf-card dstf-tree w-full gap-1").mark("tree"):
            self.line = ui.row().classes("dstf-tree-line items-center gap-3 w-full")
            self.rows = ui.column().classes("w-full gap-0").mark("tree:rows")
        # Not refreshed here: the page builds this card before it knows
        # whether the test is one of the open project's, and `_view` raises
        # for a model the project has not got. The page's own `sync` fills
        # it, or `forget` leaves it saying nothing.
        self.rows.set_visibility(False)

    def _view(self) -> dict | None:
        return self.page.shell.session.tree_view(self.page.model_id)

    def toggle(self) -> None:
        self.is_open = not self.is_open
        self.refresh(self._view())

    def forget(self) -> None:
        """The page's test is gone (another project was opened under it):
        the card says nothing rather than the last project's verdicts, and
        `refresh` is not the way there — its "nothing scored" line asks the
        page WHY, which for a test this project does not have is a sentence
        about a baseline that was never the reason."""
        for host in self.hosts:
            host.clear()
        self.hosts, self.painters, self.named = [], {}, []
        self.root_pill = None
        self.is_open = False
        self._shape = ()
        self.rows.clear()
        self.rows.set_visibility(False)
        self.line.clear()

    def refresh(self, view: dict | None) -> None:
        """Follow the Session: the line every time (it is five elements and
        the toggle's own text is one of them), the rows in place unless
        their shape moved. Open or closed is the card's and survives this.
        ``view`` is the page's ONE `tree_view` read of this sync — the cards
        draw their criteria rows from the same dict."""
        if view is None:
            self.is_open = False
        self._line(view)
        self.rows.set_visibility(self.is_open)
        if not self.is_open:
            # A form nobody can see is no form: hiding the tree takes a
            # form open under one of its rows away (the Task 5 review).
            for host in self.hosts:
                if host.row is not None:
                    self.page.drop_row(host.row)
        if view is None or not self.is_open:
            return
        shape = shape_of(view)
        if shape != self._shape or not self.painters:
            self._shape = shape
            self._build_rows(view)
            return
        for node in nodes_of(view):
            self.painters[str(node.get("path"))].paint(node)
        for host in self.hosts:
            # The rows stand; what a node's controls MAY do does not (a
            # job's lock comes and goes without the shape moving).
            host.follow()

    # -- the closed line -----------------------------------------------------------

    def _line(self, view: dict | None) -> None:
        self.line.clear()
        self.named, self.root_pill = [], None
        with self.line:
            if view is None:
                ui.label(f"nothing scored: {self.page.unscored_reason()}").classes(
                    "text-secondary"
                ).mark("tree:summary")
                return
            ui.button(
                "Hide tree" if self.is_open else "Show tree",
                icon="expand_more" if self.is_open else "chevron_right",
                on_click=self.toggle,
            ).props("flat dense no-caps").mark("tree:toggle")
            self.root_pill = Pill(root_status(view), "tree:root-pill")
            ui.label(root_text(view)).classes("text-weight-medium").mark("tree:root")
            text, self.named, more = summary_text(view)
            ui.label(text).classes("text-secondary").mark("tree:summary")
            for leaf in self.named:
                button = ui.button(
                    leaf_name(leaf),
                    on_click=partial(self.page.jump_to, str(leaf["path"])),
                ).props("flat dense no-caps")
                with button.mark("tree:jump"):
                    # Two criteria on one variable read identically ("range ·
                    # x, range · x"): the path is what tells them apart.
                    ui.tooltip(str(leaf["path"]))
            if more:
                ui.label(f"+{more} more").classes("text-secondary").mark("tree:more")

    # -- the rows ------------------------------------------------------------------

    def _build_rows(self, view: dict) -> None:
        for host in self.hosts:
            host.clear()
        self.hosts, self.painters = [], {}
        self.rows.clear()
        with self.rows:
            self._row(view, 0)

    def _row(self, node: dict, depth: int) -> None:
        path, model = str(node["path"]), self.page.model_id
        row = ui.row().classes("dstf-tree-row items-center no-wrap gap-3 w-full")
        with row.style(f"padding-left: {16 * depth}px").mark(f"tree-row:{path}"):
            pill = Pill(node_status(node), f"tree-pill:{path}")
            text = ui.label(node_text(node)).classes("text-body2")
            readout: ui.label | None = None
            if node.get("kind") == "leaf":
                readout = ui.label(leaf_readout(node)).classes("dstf-mono text-xs")
            ui.space()
            target = RowTarget(self.page, f"tree:{path}", row, path)
            host = InlineHost(
                self.page.shell,
                Placement.NODE,
                partial(Selection, tests=frozenset({model}), node_path=path),
                figure=None,
                marker=f"node:{path}",
                row=target,
            )
            # A host drawn inside a row keeps to its own width, or the
            # cluster sits left of where it belongs (the 3b review, in
            # Chromium).
            host.column.classes(remove="w-full", add="dstf-node-host")
        # The row's form slot, between it and the next node's row; the
        # host is built once its panels have somewhere to open a row.
        target.slot = ui.column().classes("w-full gap-0")
        host.build()
        self.hosts.append(host)
        self.painters[path] = _Row(pill, text, readout)
        for child in node.get("children") or []:
            self._row(child, depth + 1)
