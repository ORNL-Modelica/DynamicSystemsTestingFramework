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

from ...comparison.tree_spec import reads_a_statistic
from ...reporting.tree_rows import leaves_of
from ..capabilities import Placement
from ..session import Selection
from ..tree_move import TREE_CHANGED, MoveRefused
from ..tree_pointer import node_or_none
from ..tree_text import count_text, leaf_name, node_text, nodes_of
from .actions import InlineHost, RowTarget
from .widgets import Pill

if TYPE_CHECKING:
    from .test_page import TestPage

NAMED = 2  #: failures the closed line names; the rest are counted

#: One level's bar and the gap after it (U5, Q1): 6 px + 2 px.
BAR_STEP = 8
#: The row's gap between its parts (`gap-3`), the chevron's place and the
#: drag handle's (`.dstf-chev-slot`, `.dstf-grip`), px.
_GAP, _CHEVRON, _GRIP = 12, 28, 20


def content_left(depth: int) -> int:
    """Where a row's content starts, right of its bars: its chevron's place
    (U5, E4) — where a drag's "before" line starts (U4 C15)."""
    return BAR_STEP * depth + _GAP


def pill_left(depth: int) -> int:
    """Where a row's pill starts: past its chevron's place and its handle."""
    return content_left(depth) + _CHEVRON + _GAP + _GRIP + _GAP


#: Appended to a move's announcement when the live region already holds
#: that text: a live region is read out when its text CHANGES, and a
#: second "Moved." would not change it (`TreeCard.say`).
_AGAIN = "\u200b"

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
#:
#: The drag carries its own MIME type, never text: a drag that does not
#: (a file, a selection) draws nothing and drops nothing here, and ours,
#: released over a text field, types no path into it. It carries the
#: rows' revision from its start too (`TreeCard.revision`): a drop on rows
#: rebuilt since is refused (`TreeCard.dropped`).
_DRAG_COMMON = """const D = window.dstfMove || (window.dstfMove = {src: null, map: {}});
const MIME = __MIME__;
const ours = (e) => !!e.dataTransfer && [...e.dataTransfer.types].includes(MIME);
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
//: The drag resting on a folded group (U5, Q10): 700 ms over its row
//: unfolds it, as its chevron's click would; leaving the row, the drop
//: and the drag's end cancel it (E6).
const unhover = () => { if (D.hover) { clearTimeout(D.hover.t); D.hover = null; } };
const hover = (row) => {
  const at = row && row.dataset.folded === '1' ? row : null;
  if (D.hover && D.hover.row === at) return;
  unhover();
  if (!at) return;
  D.hover = {row: at, t: setTimeout(() => {
    D.hover = null;
    if (at.dataset.folded === '1') at.querySelector(':scope > .dstf-chev-slot > .dstf-chev')?.click();
  }, __REST__)};
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
  D.rev = e.currentTarget.dataset.rev;
  D.map = {};
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData(MIME, D.src);
  e.dataTransfer.setDragImage(row, 12, row.offsetHeight / 2);
  emit(D.src);
}"""
_DRAG_OVER = """(e) => {
  __COMMON__
  if (!D.src || !ours(e)) return;
  const row = e.target.closest && e.target.closest('[data-tree-path]');
  hover(row);
  // Over no row (a form row, a slot, a move's line): no place, nothing drawn.
  if (!row) { clear(e.currentTarget); return; }
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
  if (!e.currentTarget.contains(e.relatedTarget)) { unhover(); clear(e.currentTarget); }
}"""
_DROP = """(e) => {
  __COMMON__
  if (!ours(e)) return;
  const row = e.target.closest && e.target.closest('[data-tree-path]');
  const src = D.src;
  unhover();
  clear(e.currentTarget);
  D.src = null;
  if (!row || !src) return;
  e.preventDefault();
  const zone = zoneOf(row, e);
  if ((D.map[row.dataset.treePath] || {})[zone] != null) return;
  emit({src: src, target: row.dataset.treePath, zone: zone, rev: D.rev});
}"""
_DRAG_END = """(e) => {
  __COMMON__
  unhover();
  clear(e.currentTarget);
  D.src = null;
}"""


#: The hovered branch (U5, Y2), drawn in the browser: hovering a group's
#: row deepens that group's level bar on its row and every row under it; a
#: criterion's row does the same for its parent group; the root, and a
#: criterion right under it, deepen nothing. Marked on the BAR, never the
#: row: a server repaint of a row's classes (`row.classes`) writes the
#: row's whole class attribute and takes a client-set row class with it —
#: the U5 spike measured it — and the server never re-classes a bar.
_BRANCH = """(e) => {
  const rows = e.currentTarget;
  const row = e.target.closest && e.target.closest('[data-tree-path]');
  let group = null;
  if (row && row.dataset.root !== '1') {
    const p = row.dataset.group === '1' ? row.dataset.treePath
      : row.dataset.treePath.slice(0, row.dataset.treePath.lastIndexOf('/children/'));
    group = rows.querySelector(`[data-tree-path="${p}"]`);
    if (group && group.dataset.root === '1') group = null;
  }
  // The rows' revision in the key: rows rebuilt under a resting pointer
  // are new rows, and are marked again.
  const at = group ? group.dataset.treePath : '';
  const key = `${rows.dataset.rev}|${at}`;
  if (rows.dataset.branch === key) return;
  rows.querySelectorAll('.dstf-lvl-on').forEach(b => b.classList.remove('dstf-lvl-on'));
  rows.dataset.branch = key;
  if (!group) return;
  const k = Number(group.dataset.depth);
  const mark = (r) => {
    const b = r.querySelector(':scope > .dstf-bars').children[k - 1];
    if (b) b.classList.add('dstf-lvl-on');
  };
  mark(group);
  rows.querySelectorAll(`[data-tree-path^="${at}/"]`).forEach(mark);
}"""
_BRANCH_OFF = """(e) => {
  const rows = e.currentTarget;
  rows.querySelectorAll('.dstf-lvl-on').forEach(b => b.classList.remove('dstf-lvl-on'));
  rows.dataset.branch = '';
}"""

#: A group folded or unfolded in the page (U5, Q3): its row's
#: ``data-folded``, and every row and form slot under a folded group
#: ``hidden`` — attributes, never classes (E2): a server repaint of a
#: row's classes writes its whole class attribute (the U5 spike) and leaves
#: its other attributes alone. No rows are rebuilt, so a drag's revision
#: stays (U4 W5). The server is told, records it in the tab and sets the
#: same attributes, so a later rebuild draws them too.
_FOLD_CLICK = """(e) => {
  e.stopPropagation();  // a chevron's click is never its row's
  const row = e.target.closest('[data-tree-path]');
  if (!row) return;
  const rows = row.parentElement;
  const on = row.dataset.folded !== '1';
  if (on) row.dataset.folded = '1'; else delete row.dataset.folded;
  const chev = e.currentTarget;
  chev.setAttribute('aria-expanded', on ? 'false' : 'true');
  chev.setAttribute('aria-label', (on ? 'Unfold ' : 'Fold ') + chev.dataset.name);
  const shut = [...rows.querySelectorAll(':scope > [data-folded="1"]')].map(
    r => r.dataset.treePath + '/');
  rows.querySelectorAll(':scope > [data-tree-path], :scope > [data-slot-of]').forEach(el => {
    const p = el.dataset.treePath || el.dataset.slotOf;
    el.hidden = shut.some(s => p.startsWith(s));
  });
  emit(on);
}"""

#: A click on a row's own control is the control's, never the row's (U5):
#: the row's click opens its editor or its fields.
_STOP = "(e) => e.stopPropagation()"

#: Enter or Space on a row that is a button (U5, F5) is its click — on the
#: row itself, never bubbled up from a control inside it; Space's default
#: would scroll the page.
_ROW_KEYS = (
    "(e) => { if (e.target === e.currentTarget && "
    "(e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); emit(); } }"
)

#: The drag's own MIME type (`_DRAG_COMMON`).
MIME = "application/x-dstf-node"

#: How long a drag rests on a folded group before it unfolds (Q10), ms.
REST_MS = 700


def _drag_js(handler: str) -> str:
    return (
        handler.replace("__COMMON__", _DRAG_COMMON)
        .replace("__MIME__", json.dumps(MIME))
        .replace("__REST__", str(REST_MS))
    )


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


def fold_text(node: dict) -> str:
    """What a folded group's row adds (Q4, E6): its criteria, however deep,
    counted by verdict — "· 3 hidden · 2 pass · 1 fail", with an advisory
    and an unscored count when either is non-zero, as the closed line's
    tally. A folded group inside it is inside it: each criterion is counted
    once."""
    statuses = [leaf_status(leaf) for leaf in leaves_of(node)]
    parts = [f"{len(statuses)} hidden", f"{statuses.count('pass')} pass"]
    for status, word in (
        ("fail", "fail"),
        ("warn", "advisory"),
        ("queued", "unscored"),
    ):
        if statuses.count(status):
            parts.append(f"{statuses.count(status)} {word}")
    return "· " + " · ".join(parts)


def show_tip(leaf: dict) -> str:
    """The ⤓'s name (U5, Q7): where it shows the criterion — a statistics
    criterion is drawn on the Statistics card, any other on its plot."""
    statistic = reads_a_statistic(str(leaf.get("metric")), leaf.get("params") or {})
    return "Show on the Statistics card" if statistic else "Show on its plot"


def _ancestors(path: str) -> list[str]:
    """Every proper ancestor's path of ``path``, the root's included."""
    out = []
    while "/children/" in path:
        path = path.rsplit("/children/", 1)[0]
        out.append(path)
    return out


def _set_prop(element: ui.element, key: str, value: str | bool | None) -> bool:
    """``element``'s prop ``key`` set to ``value`` (None: removed), and
    whether it moved; the caller sends the element once."""
    if element.props.get(key) == value:
        return False
    if value is None:
        del element.props[key]
    else:
        element.props[key] = value
    return True


class _Row:
    """One node's row, and what a repaint writes into it."""

    def __init__(
        self,
        pill: Pill,
        text: ui.label,
        readout: ui.label | None,
        lead: ui.element | None = None,
        summary: ui.label | None = None,
        chevron: ui.element | None = None,
        show: tuple[ui.element, ui.tooltip] | None = None,
    ) -> None:
        self.pill, self.text, self.readout, self.lead = pill, text, readout, lead
        #: A group's fold summary (Q4), shown by CSS while its row is
        #: folded: painted every time, so a fold needs no round trip.
        self.summary = summary
        #: A group's chevron (Q3): named by the group's text.
        self.chevron = chevron
        #: A criterion's ⤓ and its tooltip (Q7): named by where it is
        #: drawn, which a mode switch moves without rebuilding the row.
        self.show = show

    def paint(self, node: dict) -> None:
        if self.summary is not None:
            self.summary.set_text(fold_text(node))
        if self.chevron is not None:
            name = node_text(node)
            if self.chevron.props.get("data-name") != name:
                self.chevron.props["data-name"] = name
                verb = (
                    "Fold"
                    if self.chevron.props.get("aria-expanded") == "true"
                    else "Unfold"
                )
                self.chevron.props["aria-label"] = f"{verb} {name}"
                self.chevron.update()
        self.pill.set(node_status(node))
        # A lead holding the kind (a combinator's dropdown) leaves the text
        # the count; an empty one — a host that cannot draw, under a job's
        # lock — leaves the kind in the text, so it never disappears.
        led = self.lead is not None and bool(self.lead.default_slot.children)
        self.text.set_text(count_text(node) if led else node_text(node))
        if self.show is not None:
            button, tip = self.show
            said = show_tip(node)
            if _set_prop(button, "aria-label", said):
                button.update()
                tip.set_text(said)
        if self.readout is not None:
            # Cut to one line with an ellipsis: its title holds the whole.
            said = leaf_readout(node)
            self.readout.set_text(said)
            if _set_prop(self.readout, "title", said):
                self.readout.update()


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
        #: The rows' revision, one up at each rebuild: what a drag carries
        #: from its start and its drop is checked against (`dropped`).
        self.revision = 0
        self._shape: tuple[tuple[str, str, str, str], ...] = ()
        #: Each group's chevron by its path (U5, Q3): the root has none.
        self.chevrons: dict[str, ui.element] = {}
        #: The criteria's rows by path: a click on each opens its editor.
        self.leaves: set[str] = set()
        #: The one criterion editor opened under a tree row (U5, Y4): its
        #: leaf, its host and box, the leaf as authored when its fields
        #: were built, and its "Edit on its plot" link's place and plot.
        self.editor_path: str | None = None
        self.editor: InlineHost | None = None
        self.editor_box: ui.column | None = None
        self.link_slot: ui.element | None = None
        self._edited: str | None = None
        self._link: int | None = None
        with ui.card().classes("dstf-card dstf-tree w-full gap-1").mark("tree"):
            self.line = ui.row().classes("dstf-tree-line items-center gap-3 w-full")
            self.rows = ui.column().classes("w-full gap-0").mark("tree:rows")
            #: What a screen reader is told of a move (`say`): ONE region,
            #: there from the start and visually hidden — a live region is
            #: read out when its text changes, and one that arrives holding
            #: its text already is not. The line under the row is not it:
            #: made for each move, it needed a timer to arrive empty first.
            self.live = (
                ui.label("")
                .classes("dstf-live")
                .props('role="status" aria-live="polite"')
                .mark("moved-live")
            )
        rows = self.rows
        rows.on("dragstart", self._drag_start, js_handler=_drag_js(_DRAG_START))
        rows.on("dragover", js_handler=_drag_js(_DRAG_OVER))
        rows.on("dragleave", js_handler=_drag_js(_DRAG_LEAVE))
        rows.on("drop", self._drop, js_handler=_drag_js(_DROP))
        rows.on("dragend", js_handler=_drag_js(_DRAG_END))
        rows.on("mouseover", js_handler=_BRANCH)
        rows.on("mouseleave", js_handler=_BRANCH_OFF)
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
        panel that registered for it there, if one did. A drag that started
        on rows rebuilt since is refused under that row: its paths name the
        nodes of a tree that is no longer there."""
        target = self.targets.get(str(args.get("target")))
        if target is None or target.on_drop is None:
            return
        if str(args.get("rev")) != str(self.revision):
            target.refuse(MoveRefused(TREE_CHANGED))
            return
        target.on_drop(str(args.get("src")), str(args.get("zone")))

    def say(self, path: str, text: str) -> None:
        """A move's line under ``path``'s row, the row brought into view and
        flashed (V4), and the same text told to the card's live region. One
        at a time; `unsay` takes the line away."""
        self.unsay()
        target, row = self.targets.get(path), self.rows_by_path.get(path)
        if target is None or target.slot is None or row is None:
            return
        self.open_path(path)  # R2: a tree event shows its row
        depth = path.count("/children/")
        with target.slot:
            # Under the row's pill: its bars, the chevron's place and the
            # handle's (E4).
            self.said = (
                ui.label(text)
                .classes("dstf-moved text-xs")
                .style(f"padding-left: {pill_left(depth)}px")
                .mark("moved-note")
            )
        self.live.set_text(text if self.live.text != text else text + _AGAIN)
        # To THIS page's client: `sync` runs from the Shell's timer, whose
        # context is no page's (`TestPage._reveal`). The row, then the line
        # under it: the row alone, scrolled to its nearest edge from above,
        # left the line below the fold.
        self.page.client.run_javascript(
            f'const r = document.getElementById("{row.html_id}");'
            f'const l = document.getElementById("{self.said.html_id}");'
            'if (r) { r.scrollIntoView({block: "nearest"});'
            ' r.classList.remove("dstf-flash"); void r.offsetWidth;'
            ' r.classList.add("dstf-flash"); }'
            'if (l) l.scrollIntoView({block: "nearest"});'
        )

    def unsay(self) -> None:
        if self.said is not None and not self.said.is_deleted:
            self.said.delete()
        self.said = None

    # -- folding (U5) --------------------------------------------------------------

    @property
    def folded(self) -> set[str]:
        """This test's folded groups, the tab's (Q8)."""
        return set(self.page.state.tree_folded.get(self.page.model_id, []))

    def _keep(self, folded: set[str]) -> None:
        store = self.page.state.tree_folded
        if folded:
            store[self.page.model_id] = sorted(folded)
        else:
            store.pop(self.page.model_id, None)

    def _apply_folds(self) -> None:
        """The rows as the tab's folded set says — the attributes the page's
        script sets, by a props update of the elements that moved and no
        rebuild (E2), so the two agree and the drag's revision stands. A
        form under a row that is now hidden goes (E6: a form nobody can see
        is no form, as Hide tree)."""
        folded = self.folded
        for path, row in self.rows_by_path.items():
            hide = any(up in folded for up in _ancestors(path))
            target = self.targets.get(path)
            slot = target.slot if target is not None else None
            if slot is not None and _set_prop(slot, "hidden", True if hide else None):
                slot.update()
            moved = _set_prop(row, "hidden", True if hide else None)
            chev = self.chevrons.get(path)
            if chev is not None:
                on = path in folded
                moved |= _set_prop(row, "data-folded", "1" if on else None)
                name = chev.props.get("data-name", "")
                turned = _set_prop(chev, "aria-expanded", "false" if on else "true")
                turned |= _set_prop(
                    chev, "aria-label", f"{'Unfold' if on else 'Fold'} {name}"
                )
                if turned:
                    chev.update()
            if moved:
                row.update()
            if hide and target is not None:
                self.page.drop_row(target)
                if self.editor_path == path:
                    # The editor under it goes the same way (F9).
                    self.page._close()

    def _fold(self, path: str, e: Any) -> None:
        """A chevron's click (or a drag resting on its row, Q10): the page
        has folded or unfolded the group already; the tab records it."""
        folded = self.folded
        on = e.args if isinstance(e.args, bool) else path not in folded
        if on:
            folded.add(path)
        else:
            folded.discard(path)
        self._keep(folded)
        self._apply_folds()

    def fold_all(self, on: bool) -> None:
        """Collapse all / Expand all (Y7): every group of the test but the
        root, which is the tree (Show / Hide tree folds it)."""
        self._keep(set(self.chevrons) if on else set())
        self._apply_folds()

    def open_path(self, path: str) -> None:
        """R2: unfold every folded group ``path`` is inside, and nothing
        else — a group at ``path`` itself stays as it is. Only for what the
        tree itself shows under a row (a move's line, a refusal, a form);
        what is done outside the tree leaves the folds alone."""
        folded = self.folded
        opened = folded - set(_ancestors(path))
        if opened != folded:
            self._keep(opened)
            self._apply_folds()

    def rekey(self, paths: dict[str, str]) -> None:
        """An edit went through (R1): every folded group is folded where the
        edit put it (``paths``: each kept node, before -> after); one the
        edit removed is dropped. Applied by the sync that redraws the rows
        — the rows standing now are the old tree's."""
        self._keep({paths[p] for p in self.folded if p in paths})

    def toggle(self) -> None:
        self.is_open = not self.is_open
        self.refresh(self._view())

    def forget(self) -> None:
        """The page's test is gone (another project was opened under it):
        the card says nothing rather than the last project's verdicts, and
        `refresh` is not the way there — its "nothing scored" line asks the
        page WHY, which for a test this project does not have is a sentence
        about a baseline that was never the reason."""
        self.close_editor()
        for host in self.hosts:
            host.clear()
        self.hosts, self.painters, self.named = [], {}, []
        self.rows_by_path, self.targets, self.chevrons = {}, {}, {}
        self.leaves = set()
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
            # And an editor nobody can see is no editor (U5).
            if self.editor is not None:
                self.page._close()
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
        # The folded set may have been re-keyed under rows whose shape did
        # not move (twin groups trading places, the U5 review): it is drawn
        # on the rows standing, not left on the ones it was folded on.
        self._apply_folds()
        # A host rebuilt under a job's lock takes its row's click away.
        self.paint_clicks()
        self._follow_editor()

    def paint_clicks(self) -> None:
        """Each row whose click opens something — a criterion's (its
        editor), a group's a panel registered one for (its fields) — is a
        button to the keyboard and says whether that is open under it now
        (U5, F5); any other row is neither. Sent only where it moved."""
        form = self.page.form[0] if self.page.form is not None else None
        for path, target in self.targets.items():
            row = target.row
            if row.is_deleted:
                continue
            on = path in self.leaves or target.click is not None
            shown = None
            if on:
                shown = (
                    "true" if self.editor_path == path or form is target else "false"
                )
            moved = _set_prop(row, "role", "button" if on else None)
            moved |= _set_prop(row, "tabindex", "0" if on else None)
            moved |= _set_prop(row, "aria-expanded", shown)
            if moved:
                row.update()

    # -- the criterion editor under a tree row (U5) -----------------------------

    def open_editor(self, path: str) -> bool:
        """``path``'s editor under its tree row: Done, the "Edit on its
        plot" link where its plot can do more, then every capability placed
        at TREE_CRITERION (`PlotCard.open_editor`'s pattern with no plot).
        The page keeps the one-editor rule: it closes what else is open
        first. False where no row is drawn at ``path``."""
        target, row = self.targets.get(path), self.rows_by_path.get(path)
        if target is None or row is None or target.slot is None:
            return False
        self.close_editor()
        self.open_path(path)  # R2: an editor under a tree row is shown
        model = self.page.model_id
        self.editor_path = path
        row.classes(add="dstf-crit-open")
        with target.slot:
            self.editor_box = ui.column().classes("dstf-editor gap-2")
        with self.editor_box:
            with ui.row().classes("items-center no-wrap gap-3 w-full"):
                ui.label(
                    "Edits are staged as you make them; Save writes them."
                ).classes("dstf-why")
                ui.space()
                self.link_slot = ui.element("span")
                ui.button("Done", on_click=self.page.close_editor).props(
                    "flat dense no-caps"
                ).mark(f"tree-editor-done:{path}")
            self.editor = InlineHost(
                self.page.shell,
                Placement.TREE_CRITERION,
                partial(Selection, tests=frozenset({model}), leaf_path=path),
                figure=None,
                marker=f"tree-editor:{path}",
                on_applied=self._applied,
                apply=self.page.apply,
            )
            self.editor.build()
        self._edited = self._edited_now()
        self._link = None
        self._paint_link()
        self.paint_clicks()
        return True

    def close_editor(self) -> None:
        """The editor goes, its column only: the row's slot also holds its
        host's error labels and a move's line."""
        if self.editor is not None:
            self.editor.clear()
        if self.editor_box is not None and not self.editor_box.is_deleted:
            self.editor_box.delete()
        row = self.rows_by_path.get(self.editor_path or "")
        if row is not None and not row.is_deleted:
            row.classes(remove="dstf-crit-open")
        was = self.editor_path
        self.editor, self.editor_box, self.link_slot = None, None, None
        self.editor_path, self._edited, self._link = None, None, None
        if was is not None:
            self.paint_clicks()

    def _applied(self, name: str) -> None:
        """A panel's own submit rebuilds every OTHER panel, leaving the one
        being typed in; the leaf recorded as it now stands, so its own edit
        is not read as one staged elsewhere."""
        if self.editor is None:
            return
        self.editor.build(skip=name)
        self._edited = self._edited_now()

    def _edited_now(self) -> str | None:
        """The open editor's leaf as AUTHORED — staged edits included (the
        Statistics card's rule: a test with nothing compared has no view)."""
        if self.editor_path is None:
            return None
        session = self.page.shell.session
        leaf = node_or_none(session.tree(self.page.model_id), self.editor_path)
        return None if leaf is None else json.dumps(leaf, sort_keys=True, default=str)

    def _follow_editor(self) -> None:
        if self.editor is None:
            return
        edited = self._edited_now()
        if edited != self._edited:
            # The leaf moved by an edit staged ELSEWHERE (a Discard, its
            # plot's drag): fields showing the undone value stage it back.
            self.editor.build()
            self._edited = edited
        else:
            self.editor.follow()
        self._paint_link()

    def _paint_link(self) -> None:
        """Q12 (F2): "Edit on its plot" while the plot that holds the
        criterion — the first, as a jump opens it — offers more than this
        editor does: a capability placed at CRITERION and not here is
        available there (one that drags its handles). Asked of the
        placements, never of a capability by name; redrawn only when its
        answer moves (a mode switched to one with no handles takes it)."""
        if self.link_slot is None or self.link_slot.is_deleted:
            return
        path = self.editor_path
        page, shell = self.page, self.page.shell
        index = next((c.index for c in page.cards if path in c.rows), None)
        if index is not None and path is not None:
            selection = Selection(
                tests=frozenset({page.model_id}),
                variable=page.cards[index].rows[path].variable,
                leaf_path=path,
            )
            more = any(
                avail is True and Placement.TREE_CRITERION not in cap.placements
                for cap, avail in shell.placed(
                    Placement.CRITERION, shell.view_for(selection)
                )
            )
            if not more:
                index = None
        if index == self._link:
            return
        self._link = index
        self.link_slot.clear()
        if index is None or path is None:
            return
        with self.link_slot:
            ui.button(
                "Edit on its plot",
                on_click=partial(page.edit_on_plot, path, index),
            ).props("flat dense no-caps").mark(f"tree-editor-on-plot:{path}")

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
            if self.is_open and any(
                n.get("kind") != "leaf" for n in nodes_of(view)[1:]
            ):
                # Y7: beside the tally, while the rows show and hold a group.
                # Icons (H4, the U5 review): their words wrapped the line at
                # 900 px. Named for a screen reader and by a tooltip.
                for icon, on, name, marker in (
                    ("unfold_more", False, "Expand all groups", "tree:expand-all"),
                    ("unfold_less", True, "Collapse all groups", "tree:collapse-all"),
                ):
                    button = (
                        ui.button(icon=icon, on_click=partial(self.fold_all, on))
                        .props("flat dense round size=sm")
                        .mark(marker)
                    )
                    button.props["aria-label"] = name
                    with button:
                        ui.tooltip(name)
            self._reset_layout()

    # -- the rows ------------------------------------------------------------------

    def _build_rows(self, view: dict) -> None:
        if self.editor is not None:
            # The rows it hangs under go, and the page's one editor with
            # them: the node at its path may be another criterion now.
            self.page._close()
        for host in self.hosts:
            host.clear()
        self.hosts, self.painters, self.rows_by_path = [], {}, {}
        self.targets, self.chevrons, self.leaves = {}, {}, set()
        # A folded path that names no group any more is dropped (Y6): an
        # edit that said where its nodes went has re-keyed the set already
        # (`rekey`); this is for any other.
        groups = {
            str(n.get("path")) for n in nodes_of(view)[1:] if n.get("kind") != "leaf"
        }
        self._keep(self.folded & groups)
        self.rows.clear()
        self.revision += 1
        self.rows.props["data-rev"] = str(self.revision)
        self.rows.update()
        with self.rows:
            self._row(view, 0, self.folded)
        self.paint_clicks()

    def _show_icon(self, node: dict, path: str) -> tuple[ui.element, ui.tooltip]:
        """⤓ (Q7), at the start of the criterion's icon cluster: the
        criterion where it is drawn — its plot's row, or its Statistics
        row — scrolled to and flashed; nothing opens. The page owns the
        jump, as it owns the closed line's named buttons. Its click is
        stopped in the browser: the row's own click opens the editor."""
        tip = show_tip(node)
        button = (
            ui.button(icon="vertical_align_bottom")
            .props("flat dense round size=sm")
            .classes("dstf-show")
            .on(
                "click",
                partial(self.page.show_criterion, path),
                js_handler="(e) => { e.stopPropagation(); emit(); }",
            )
        )
        # Named for a screen reader: its icon's ligature names nothing.
        button.props["aria-label"] = tip
        with button:
            tooltip = ui.tooltip(tip).props('anchor="top middle" self="bottom middle"')
        button.mark(f"show:{path}")
        return button, tooltip

    def _row(self, node: dict, depth: int, folded: set[str]) -> None:
        path, model = str(node["path"]), self.page.model_id
        group = node.get("kind") != "leaf"
        hide = any(up in folded for up in _ancestors(path))
        row = ui.row().classes("dstf-tree-row items-center no-wrap gap-3 w-full")
        self.rows_by_path[path] = row
        # What the page's scripts read: the row's node, its depth and
        # whether it is a group (the hovered branch, U5 Y2), and the root's
        # one drop zone.
        row.props["data-tree-path"] = path
        row.props["data-depth"] = str(depth)
        if group:
            row.props["data-group"] = "1"
        if depth == 0:
            row.props["data-root"] = "1"
        # The tab's folds, drawn at build as the page's script draws them.
        if hide:
            row.props["hidden"] = True
        if path in folded:
            row.props["data-folded"] = "1"
        # The row IS the way in (U5): a criterion's editor opens under it,
        # a k-of-n's or a weighted's fields — whatever a panel registered
        # (`Panel.on_click`) — and Enter or Space does the same where the
        # row is a button (`paint_clicks`). Its own controls stop the click
        # before it gets here: the chevron, the grip, the lead (the kind
        # dropdown) and the host.
        row.on("click", partial(self.page.tree_row_click, path))
        row.on("keydown", partial(self.page.tree_row_click, path), js_handler=_ROW_KEYS)
        if not group:
            self.leaves.add(path)
        # `--indent` is where a "before" line starts: the row's content,
        # right of its bars (U4 C15, U5 E4).
        with row.style(f"--indent: {content_left(depth)}px").mark(f"tree-row:{path}"):
            # A bar per level (U5, Q1): what says what is under what, in
            # place of an indent (Y3). Level i's colour cycles every four;
            # the root draws none.
            with (
                ui.element("span")
                .classes("dstf-bars")
                .style(f"width: {BAR_STEP * depth}px")
                .props('aria-hidden="true"')
            ):
                for i in range(1, depth + 1):
                    ui.element("span").classes(f"dstf-bar dstf-lvl-{(i - 1) % 4 + 1}")
            # The chevron's place (Q3), kept on every row — empty on a
            # criterion — so the pills stay in line. The root's folds with
            # the card's Show / Hide tree.
            chevron: ui.element | None = None
            with ui.element("span").classes("dstf-chev-slot"):
                if group and depth:
                    on, name = path in folded, node_text(node)
                    chevron = (
                        ui.element("button")
                        .classes("dstf-chev")
                        .props('type="button"')
                        .mark(f"fold:{path}")
                    )
                    # Set as items, not through the props string, whose
                    # parser a quote in a node's text would break.
                    chevron.props["data-name"] = name
                    chevron.props["aria-expanded"] = "false" if on else "true"
                    chevron.props["aria-label"] = f"{'Unfold' if on else 'Fold'} {name}"
                    with chevron:
                        ui.icon("expand_more").props('aria-hidden="true"')
                    chevron.on(
                        "click", partial(self._fold, path), js_handler=_FOLD_CLICK
                    )
                    self.chevrons[path] = chevron
            # The drag handle's place, before the pill: hidden — its width
            # kept, so every pill stays in line — until a panel makes the
            # row draggable (`Panel.movable`). The root's holds no handle:
            # nothing sits beside it. Hidden by its own class, never
            # `set_visibility`: Quasar's `.hidden` sits in a cascade layer
            # that beats an unlayered `!important`, and took the width too.
            grip = (
                ui.element("span")
                .classes("dstf-grip dstf-grip-off")
                .mark(f"grip:{path}")
            )
            grip.on("click", js_handler=_STOP)
            if depth:
                grip.props["draggable"] = "true"
                grip.props["aria-label"] = "Drag to move"
                grip.props["title"] = "Drag to move"
                with grip:
                    ui.icon("drag_indicator").props('aria-hidden="true"')
            pill = Pill(node_status(node), f"tree-pill:{path}")
            # A combinator's lead place, right after its pill: where its
            # kind is read (`Panel.lead`) — a leaf has none.
            lead: ui.element | None = None
            if group:
                lead = ui.element("span").classes("dstf-node-lead")
                lead.on("click", js_handler=_STOP)
            text = (
                ui.label(node_text(node))
                .classes("dstf-tree-text text-body2")
                .mark(f"tree-text:{path}")
            )
            if depth == 0:
                # Q2: "and · 5 criteria · root" — the "· " is the tag's CSS.
                ui.label("root").classes("dstf-root-tag").mark("tree:root-tag")
            summary: ui.label | None = None
            if chevron is not None:
                summary = (
                    ui.label(fold_text(node))
                    .classes("dstf-fold-summary")
                    .mark(f"tree-summary:{path}")
                )
            readout: ui.label | None = None
            show: tuple[ui.element, ui.tooltip] | None = None
            if node.get("kind") == "leaf":
                readout = ui.label(leaf_readout(node)).classes(
                    "dstf-tree-readout dstf-mono text-xs"
                )
            ui.space()
            if not group:
                show = self._show_icon(node, path)
            target = RowTarget(self.page, f"tree:{path}", row, path, None)
            target.lead = lead
            # The tree card's rows are where a node moves from (V2).
            target.drag = True
            target.grip = grip if depth else None
            # And where a click is a panel's to say (Q6, `Panel.on_click`).
            target.clickable = True
            self.targets[path] = target
            host = InlineHost(
                self.page.shell,
                Placement.NODE,
                partial(Selection, tests=frozenset({model}), node_path=path),
                figure=None,
                marker=f"node:{path}",
                row=target,
                apply=self.page.apply,
            )
            # A host drawn inside a row keeps to its own width, or the
            # cluster sits left of where it belongs (the 3b review, in
            # Chromium).
            host.column.classes(remove="w-full", add="dstf-node-host")
        # The row's form slot, between it and the next node's row; the
        # host is built once its panels have somewhere to open a row.
        target.slot = ui.column().classes("w-full gap-0")
        # What the fold's script hides with the row (`_FOLD_CLICK`).
        target.slot.props["data-slot-of"] = path
        if hide:
            target.slot.props["hidden"] = True
        host.build()
        self.hosts.append(host)
        self.painters[path] = _Row(pill, text, readout, lead, summary, chevron, show)
        # Once the host has drawn: whether its lead holds the kind decides
        # the text.
        self.painters[path].paint(node)
        for child in node.get("children") or []:
            self._row(child, depth + 1, folded)
