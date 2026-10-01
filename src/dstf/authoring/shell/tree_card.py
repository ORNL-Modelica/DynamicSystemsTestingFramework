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

import json
from functools import partial
from typing import TYPE_CHECKING, Any

from nicegui import ui

from ...reporting.tree_rows import leaves_of
from ..capabilities import Placement
from ..session import Selection
from ..tree_text import count_text, leaf_name, node_text, nodes_of
from .actions import InlineHost, RowTarget
from .widgets import Pill

if TYPE_CHECKING:
    from .test_page import TestPage

NAMED = 2  #: failures the closed line names; the rest are counted

#: The drag (U4), drawn in the browser — only the drag's start and the drop
#: reach the server. A row's top half is BEFORE it, its bottom half ONTO
#: it (the owner's ruling, D1 as amended); the root has only onto. The map
#: of each row's zones (`Panel.movable`'s ``zones``) arrives once per drag
#: (`TreeCard._drag_start`) and is emptied at every start, so a slow reply
#: never draws one drag with the last one's map; until it has arrived a
#: zone draws as allowed, and a drop there is the act's to refuse. A
#: refused zone outlines its row and says why in the card's header line,
#: in place of the line's text — said in the row, it reflowed the row's
#: icons, and the browser shows no title while it drags.
_DRAG_COMMON = """const D = window.dstfMove || (window.dstfMove = {src: null, map: {}});
const zoneOf = (row, e) => {
  if (row.dataset.root === '1') return 'onto';
  const r = row.getBoundingClientRect();
  return (e.clientY - r.top) / r.height < 0.5 ? 'before' : 'onto';
};
const say = (rows, why) => {
  const line = rows.closest('.dstf-tree')?.querySelector('.dstf-tree-line');
  if (!line) return;
  if (why) { line.dataset.why = why; line.classList.add('dstf-line-why'); }
  else { delete line.dataset.why; line.classList.remove('dstf-line-why'); }
};
const clear = (rows) => {
  rows.querySelectorAll('.dstf-drop').forEach(el => el.classList.remove(
    'dstf-drop', 'dstf-drop-before', 'dstf-drop-onto', 'dstf-drop-no'));
  say(rows, null);
};"""
_DRAG_START = """(e) => {
  __COMMON__
  const grip = e.target.closest && e.target.closest('.dstf-grip');
  const row = grip && grip.closest('[data-tree-path]');
  if (!row) return;
  D.src = row.dataset.treePath;
  D.map = {};
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', D.src);
  e.dataTransfer.setDragImage(row, 12, row.offsetHeight / 2);
  emit(D.src);
}"""
_DRAG_OVER = """(e) => {
  __COMMON__
  const row = e.target.closest && e.target.closest('[data-tree-path]');
  if (!row || !D.src) return;
  const zone = zoneOf(row, e);
  const why = (D.map[row.dataset.treePath] || {})[zone];
  clear(e.currentTarget);
  row.classList.add('dstf-drop');
  if (why === '') return;  // where it already is: nothing drawn, no drop
  if (why) {
    row.classList.add('dstf-drop-no');
    say(e.currentTarget, why);
    e.dataTransfer.dropEffect = 'none';
    return;
  }
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  row.classList.add('dstf-drop-' + zone);
}"""
_DRAG_LEAVE = """(e) => {
  __COMMON__
  if (!e.currentTarget.contains(e.relatedTarget)) clear(e.currentTarget);
}"""
_DROP = """(e) => {
  __COMMON__
  const row = e.target.closest && e.target.closest('[data-tree-path]');
  const src = D.src;
  clear(e.currentTarget);
  D.src = null;
  if (!row || !src) return;
  e.preventDefault();
  const zone = zoneOf(row, e);
  if ((D.map[row.dataset.treePath] || {})[zone] != null) return;
  emit({src: src, target: row.dataset.treePath, zone: zone});
}"""
_DRAG_END = """(e) => {
  __COMMON__
  clear(e.currentTarget);
  D.src = null;
}"""


def _drag_js(handler: str) -> str:
    return handler.replace("__COMMON__", _DRAG_COMMON)


def shape_of(view: dict | None) -> tuple[tuple[str, str, str, str], ...]:
    """What a row is drawn from and cannot be repainted through. The paths
    are what decides the rows — one per node, painted by its path — and the
    kind and the leaf's variable ride along harmlessly: a row is built for
    that node's selection and controls, so a row whose kind or variable
    moved is one worth rebuilding anyway. The combinator is in it because
    the controls read it: `+` is grey under a warn or a weighted, and a
    node edited from weighted to and kept a grey `+` with the weighted
    reason (the Task 6 review). The metric is NOT in it: a mode switch
    repaints the row's name in place."""
    return tuple(
        (
            str(node.get("path")),
            str(node.get("kind")),
            str(node.get("variable")),
            str(node.get("combinator")),
        )
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

    def __init__(
        self,
        pill: Pill,
        text: ui.label,
        readout: ui.label | None,
        lead: ui.element | None = None,
    ) -> None:
        self.pill, self.text, self.readout, self.lead = pill, text, readout, lead

    def paint(self, node: dict) -> None:
        self.pill.set(node_status(node))
        # A lead holding the kind (a combinator's dropdown) leaves the text
        # the count; an empty one — a host that cannot draw, under a job's
        # lock — leaves the kind in the text, so it never disappears.
        led = self.lead is not None and bool(self.lead.default_slot.children)
        self.text.set_text(count_text(node) if led else node_text(node))
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
        #: Each node's row by its path: what a reveal brings into view.
        self.rows_by_path: dict[str, ui.row] = {}
        #: Each node's row target by its path: where a drop lands and a
        #: drag's zones come from (`Panel.movable`).
        self.targets: dict[str, RowTarget] = {}
        #: A move's line, under the moved row (`say`).
        self.said: ui.label | None = None
        self._shape: tuple[tuple[str, str, str, str], ...] = ()
        with ui.card().classes("dstf-card dstf-tree w-full gap-1").mark("tree"):
            self.line = ui.row().classes("dstf-tree-line items-center gap-3 w-full")
            self.rows = ui.column().classes("w-full gap-0").mark("tree:rows")
        rows = self.rows
        rows.on("dragstart", self._drag_start, js_handler=_drag_js(_DRAG_START))
        rows.on("dragover", js_handler=_drag_js(_DRAG_OVER))
        rows.on("dragleave", js_handler=_drag_js(_DRAG_LEAVE))
        rows.on("drop", self._drop, js_handler=_drag_js(_DROP))
        rows.on("dragend", js_handler=_drag_js(_DRAG_END))
        # Not refreshed here: the page builds this card before it knows
        # whether the test is one of the open project's, and `_view` raises
        # for a model the project has not got. The page's own `sync` fills
        # it, or `forget` leaves it saying nothing.
        self.rows.set_visibility(False)

    def _view(self) -> dict | None:
        return self.page.shell.session.tree_view(self.page.model_id)

    # -- the drag ------------------------------------------------------------------

    def zones_for(self, src: str) -> dict[str, dict[str, str | None]]:
        """What a drag from ``src``'s row draws over every row: its panel's
        answer, read NOW — the tree may have moved since the rows were
        built — and nothing where no panel made that row draggable."""
        target = self.targets.get(src)
        if target is None or target.zones is None:
            return {}
        return target.zones()

    def _drag_start(self, e: Any) -> None:
        src = str(e.args)
        zones = self.zones_for(src)
        # Only while that drag is the browser's: a reply arriving after
        # another drag started would draw it with this one's map.
        self.page.client.run_javascript(
            f"if (window.dstfMove && window.dstfMove.src === {json.dumps(src)})"
            f" window.dstfMove.map = {json.dumps(zones)};"
        )

    def _drop(self, e: Any) -> None:
        self.dropped(e.args)

    def dropped(self, args: dict) -> None:
        """A drop the browser allowed: the row it landed on hands it to the
        panel that registered for it there, if one did."""
        target = self.targets.get(str(args.get("target")))
        if target is None or target.on_drop is None:
            return
        target.on_drop(str(args.get("src")), str(args.get("zone")))

    def say(self, path: str, text: str) -> None:
        """A move's line under ``path``'s row, the row brought into view and
        flashed (V4). One at a time; `unsay` takes it away."""
        self.unsay()
        target, row = self.targets.get(path), self.rows_by_path.get(path)
        if target is None or target.slot is None or row is None:
            return
        depth = path.count("/children/")
        with target.slot:
            # Under the row's pill: its indent, the handle's place and a gap.
            self.said = (
                ui.label(text)
                .classes("dstf-moved text-xs")
                .style(f"padding-left: {16 * depth + 32}px")
                .props('role="status"')
                .mark("moved-note")
            )
        # To THIS page's client: `sync` runs from the Shell's timer, whose
        # context is no page's (`TestPage._reveal`).
        self.page.client.run_javascript(
            f'const r = document.getElementById("{row.html_id}");'
            'if (r) { r.scrollIntoView({block: "nearest"});'
            ' r.classList.remove("dstf-flash"); void r.offsetWidth;'
            ' r.classList.add("dstf-flash"); }'
        )

    def unsay(self) -> None:
        if self.said is not None and not self.said.is_deleted:
            self.said.delete()
        self.said = None

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
        self.rows_by_path, self.targets = {}, {}
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
        for host in self.hosts:
            # The rows stand; what a node's controls MAY do does not (a
            # job's lock comes and goes without the shape moving). Before
            # the paint: a host rebuilt under a lock empties its row's
            # lead, and the paint then reads the kind into the text.
            host.follow()
        for node in nodes_of(view):
            self.painters[str(node.get("path"))].paint(node)

    # -- the closed line -----------------------------------------------------------

    def _reset_layout(self) -> None:
        """Reset layout, at the line's end while the test's EFFECTIVE entry
        has a ``plots`` key (the owner's ruling, Q8 A) — on both of the
        line's branches: a test with no tree can have extras. It takes the
        context series and the watch plots; the derived plots are the
        tree's, and stay."""
        page = self.page
        if "plots" not in (page.shell.session.spec.entry(page.model_id) or {}):
            return
        ui.space()
        with (
            ui.button("Reset layout", on_click=page.reset_layout)
            .props("flat dense no-caps")
            .mark("tree:reset-layout")
        ):
            ui.tooltip(
                "Stage removing this test's plots key: its context series and "
                "watch plots go"
            )

    def _line(self, view: dict | None) -> None:
        self.line.clear()
        self.named, self.root_pill = [], None
        with self.line:
            if view is None:
                ui.label(f"nothing scored: {self.page.unscored_reason()}").classes(
                    "text-secondary"
                ).mark("tree:summary")
                self._reset_layout()
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
            self._reset_layout()

    # -- the rows ------------------------------------------------------------------

    def _build_rows(self, view: dict) -> None:
        for host in self.hosts:
            host.clear()
        self.hosts, self.painters, self.rows_by_path = [], {}, {}
        self.targets = {}
        self.rows.clear()
        with self.rows:
            self._row(view, 0)

    def _row(self, node: dict, depth: int) -> None:
        path, model = str(node["path"]), self.page.model_id
        row = ui.row().classes("dstf-tree-row items-center no-wrap gap-3 w-full")
        self.rows_by_path[path] = row
        # What the drag's script reads: the row's node, and the root's one
        # zone. `--indent` is where a "before" line starts (its depth).
        row.props["data-tree-path"] = path
        if depth == 0:
            row.props["data-root"] = "1"
        indent = f"padding-left: {16 * depth}px; --indent: {16 * depth}px"
        with row.style(indent).mark(f"tree-row:{path}"):
            # The drag handle's place, before the pill: hidden — its width
            # kept, so every pill stays in line — until a panel makes the
            # row draggable (`Panel.movable`). The root's holds no handle:
            # nothing sits beside it.
            grip = ui.element("span").classes("dstf-grip").mark(f"grip:{path}")
            if depth:
                grip.props["draggable"] = "true"
                grip.props["aria-label"] = "Drag to move"
                grip.props["title"] = "Drag to move"
                with grip:
                    ui.icon("drag_indicator").props('aria-hidden="true"')
            grip.set_visibility(False)
            pill = Pill(node_status(node), f"tree-pill:{path}")
            # A combinator's lead place, right after its pill: where its
            # kind is read (`Panel.lead`) — a leaf has none.
            lead: ui.element | None = None
            if node.get("kind") != "leaf":
                lead = ui.element("span").classes("dstf-node-lead")
            text = (
                ui.label(node_text(node))
                .classes("text-body2")
                .mark(f"tree-text:{path}")
            )
            readout: ui.label | None = None
            if node.get("kind") == "leaf":
                readout = ui.label(leaf_readout(node)).classes("dstf-mono text-xs")
            ui.space()
            target = RowTarget(self.page, f"tree:{path}", row, path, None)
            target.lead = lead
            # The tree card's rows are where a node moves from (V2).
            target.drag = True
            target.grip = grip if depth else None
            self.targets[path] = target
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
        self.painters[path] = _Row(pill, text, readout, lead)
        # Once the host has drawn: whether its lead holds the kind decides
        # the text.
        self.painters[path].paint(node)
        for child in node.get("children") or []:
            self._row(child, depth + 1)
