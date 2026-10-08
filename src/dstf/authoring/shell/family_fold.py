"""A plot card's folded criteria rows (D152; the owner's R2): under an
array family's plot, the elements whose criterion is one and the same read
as ONE row — the metric, the family with the folded indices, a count by
verdict — whose chevron unfolds it to its members' own rows and whose click
opens an editor that changes every member at once. Which rows fold is
`authoring.family_rows.fold_rows`' rule; this draws them and knows none of
it.

The fold is DERIVED from the tree at every paint, and nothing remembers it:
a member whose criterion is edited is its own row at the next paint, and
one edited back folds again. So nothing here is rebuilt for it. The
criteria's rows stand where the card drew them, flat and in tree order; a
fold's row is put in before its first member's, and a member is that same
row with two attributes — ``data-member`` (its indent) and ``hidden``
(while its fold is closed). Attributes, never classes: a server repaint of
a row's classes writes its whole class attribute (the U5 spike), and both
survive every repaint. An editor being typed in is therefore never
re-parented or redrawn by a row joining or leaving a fold.

Which folds are open is the browser tab's (`TabState.family_unfolded`),
kept as their members' paths so it follows the members — a fold whose first
member left it is still the fold that was opened."""

from __future__ import annotations

import json
from functools import partial
from typing import TYPE_CHECKING, Any

from nicegui import ui

from ...discovery.unit_tests import is_unit_test_variable
from ...reporting.tree_rows import criteria_rows
from ..capabilities import Placement
from ..family_rows import Fold, criterion_of, fold_rows, is_fold, leaf_of, tally
from ..session import Selection
from ..spec_document import json_equal
from .actions import InlineHost, RowTarget
from .tree_card import _set_prop, leaf_status
from .widgets import MiddleCut, Pill

if TYPE_CHECKING:
    from .plot_card import PlotCard

#: A folded row's editor hint (the controller's D3): fields only. A handle
#: on the plot belongs to ONE criterion's drawing, and twenty of them at
#: one place could not be told apart or dragged as one.
HINT = "These fields change all {n} criteria. Unfold to drag one on the plot."


class _NoControls:
    """A folded row's place in `PlotCard.rows`' ``host``: it has no node
    controls — `+`, wrap and `−` act on ONE node, and are its members'."""

    def build(self) -> None:
        pass

    def follow(self) -> None:
        pass

    def clear(self) -> None:
        pass


class FoldRow:
    """One folded row. It stands in `PlotCard.rows` under its key
    (`family_rows.fold_key`) beside the criteria's rows, so the page opens,
    reveals, closes and scrolls to it exactly as it does theirs; what a
    criterion's row has and this has not (`taker`, `host`) is here as
    nothing."""

    def __init__(self, folding: Folding, fold: Fold, crumb_ch: int) -> None:
        card = folding.card
        index, key = card.index, fold.key
        self.folding, self.key, self.fold = folding, key, fold
        self.taker: str | None = None
        self.host = _NoControls()
        self.variable = fold.variables[0]
        #: What the open editor's fields were built from (`_stamp`).
        self._edited: str | None = None
        self.row = ui.row().classes("dstf-crit dstf-fold items-center gap-3 w-full")
        # The row IS the way in, as a criterion's is: its editor opens below
        # it, and a second click closes it.
        self.row.on("click", partial(card.page.row_click, index, key))
        with self.row.mark(f"family:{index}:{key}"):
            # In the swatch's place, so every row's name starts in one
            # column. A button: Tab reaches it, Enter and Space toggle it.
            self.chevron = (
                ui.element("button")
                .classes("dstf-chev dstf-fold-chev")
                .props('type="button"')
                .mark(f"family-chev:{index}:{key}")
                .on(
                    "click",
                    partial(folding.toggle, key),
                    js_handler="(e) => { e.stopPropagation(); emit(); }",
                )
            )
            with self.chevron:
                ui.icon("expand_more").props('aria-hidden="true"')
            if fold.crumbs:
                ui.label(fold.crumbs).classes("dstf-crumb dstf-why").style(
                    f"min-width: {crumb_ch}ch"
                )
            self.pill = Pill("queued", f"family-pill:{index}:{key}")
            self.name = MiddleCut().classes(
                "dstf-crit-name text-body2 text-weight-medium"
            )
            self.count = (
                ui.label()
                .classes("dstf-mono text-xs")
                .mark(f"family-count:{index}:{key}")
            )
        self.slot = ui.column().classes("w-full gap-0")
        self.target = RowTarget(
            card.page, f"family:{index}:{key}", self.row, leaf_of(key), index
        )
        self.target.slot = self.slot

    def paint(self, fold: Fold, shut: bool) -> None:
        from .plot_card import worst  # plot_card imports this module

        self.fold = fold
        statuses = [leaf_status(leaf) for leaf in fold.leaves]
        self.pill.set(worst(statuses))
        self.name.set_text(fold.name)
        self.count.set_text(tally(statuses))
        if _set_prop(self.row, "data-folded", "1" if shut else None):
            self.row.update()
        moved = _set_prop(self.chevron, "aria-expanded", "false" if shut else "true")
        moved |= _set_prop(
            self.chevron, "aria-label", f"{'Unfold' if shut else 'Fold'} {fold.name}"
        )
        if moved:
            self.chevron.update()
        card = self.folding.card
        if card.open_path == self.key and card.hint is not None:
            card.hint.set_text(self._hint())

    # -- its editor ---------------------------------------------------------------

    def _hint(self) -> str:
        from .plot_card import STAGED  # plot_card imports this module

        return HINT.format(n=len(self.fold.paths)) + STAGED

    def stamp(self, view: dict | None) -> str | None:
        """The criterion its members share, as ``view`` shows it — what
        the open editor's fields say. ``None`` when they no longer share
        one, or are no longer all there: the editor has nothing to show."""
        wanted = set(self.fold.paths)
        rows = criteria_rows(view, self.folding.card.subject)
        leaves = [row["leaf"] for row in rows if str(row["leaf"]["path"]) in wanted]
        if len(leaves) != len(wanted):
            return None
        criteria = [criterion_of(leaf) for leaf in leaves]
        if not all(json_equal(criteria[0], other) for other in criteria[1:]):
            return None
        return json.dumps(criteria[0], sort_keys=True, default=str)

    def open_editor(self, view: dict | None) -> None:
        """Its editor, below it, in the card's own editor fields — so
        `PlotCard.close_editor`, the page's one-editor rule and its scroll
        find it as they find a criterion's: the hint line and Done, then
        every capability placed at FAMILY_CRITERION. No figure: nothing of
        a fold is dragged."""
        card, index, key = self.folding.card, self.folding.card.index, self.key
        model = card.page.model_id
        card.open_path, card._edited = key, None
        self.row.classes(add="dstf-crit-open")
        with self.slot:
            card.editor_box = ui.column().classes("dstf-editor w-full gap-2")
        with card.editor_box:
            with ui.row().classes("items-center no-wrap gap-3 w-full"):
                card.hint = (
                    ui.label(self._hint())
                    .classes("dstf-why")
                    .mark(f"editor-hint:{index}:{key}")
                )
                ui.space()
                ui.button("Done", on_click=card.page.close_editor).props(
                    "flat dense no-caps"
                ).mark(f"editor-done:{index}:{key}")
            # Where the host says a refusal: above the fields it is about.
            self.target.slot = ui.column().classes("w-full gap-0")
            card.editor = InlineHost(
                card.page.shell,
                Placement.FAMILY_CRITERION,
                # Read at every build: the members are the fold's as it is.
                lambda: Selection(
                    tests=frozenset({model}),
                    variable=self.fold.variables[0],
                    leaf_path=self.fold.paths[0],
                    leaf_paths=self.fold.paths,
                ),
                figure=None,
                marker=f"editor:{index}:{key}",
                on_applied=self._applied,
                row=self.target,
                apply=card.page.apply,
            )
            card.editor.build()
        self._edited = self.stamp(view)

    def _applied(self, name: str) -> None:
        """A panel's own submit: recorded from a FRESH tree view, so its
        edit is not read as one staged elsewhere (`PlotCard._applied`'s
        rule). No panel is rebuilt: neither shows what the other edits,
        and an Indices edit redraws the whole card."""
        self._edited = self.stamp(self.folding.card._view())

    def follow(self, view: dict | None) -> bool:
        """The open editor after a paint: False when its members no longer
        share one criterion — a Discard, a Save, another tab's edit took
        them apart — and it has to close; rebuilt when the criterion they
        share moved by an edit staged ELSEWHERE, whose fields would stage
        the undone value straight back."""
        stamp = self.stamp(view)
        if stamp is None:
            return False
        editor = self.folding.card.editor
        if stamp != self._edited and editor is not None:
            editor.build()
            self._edited = stamp
        return True


class Folding:
    """One card's folds: `PlotCard` builds it after its criteria rows and
    calls `paint` wherever it paints them."""

    def __init__(self, card: PlotCard, column: ui.column, crumb_ch: int) -> None:
        self.card, self.column, self.crumb_ch = card, column, crumb_ch
        #: Each member's fold, by the member's path, as last painted.
        self.fold_of: dict[str, Fold] = {}

    @property
    def rows(self) -> dict[str, Any]:
        """`PlotCard.rows` as it IS: the criteria's rows by path, and each
        `FoldRow` by its key among them (its docstring) — the card's own
        annotation names the criteria's rows alone."""
        return self.card.rows

    # -- the tab's record ---------------------------------------------------------

    @property
    def unfolded(self) -> set[str]:
        """The member paths of this test's unfolded folds, the tab's."""
        page = self.card.page
        return set(page.state.family_unfolded.get(page.model_id, []))

    def _keep(self, unfolded: set[str]) -> None:
        page = self.card.page
        if unfolded:
            page.state.family_unfolded[page.model_id] = sorted(unfolded)
        else:
            page.state.family_unfolded.pop(page.model_id, None)

    # -- what is open on this card ------------------------------------------------

    def _open(self) -> str | None:
        """What the page's one editor is open on, when it is on this card:
        a fold's key or a criterion's path."""
        is_open = self.card.page.open
        if is_open is None or is_open[0] != self.card.index:
            return None
        return is_open[1]

    def _held(self) -> set[str]:
        """The member rows something is open under — the page's editor or
        its form row: their fold is shown, or what is open under them is
        open under nothing."""
        held = set()
        opened = self._open()
        if opened is not None and not is_fold(opened):
            held.add(opened)
        form = self.card.page.form
        if form is not None:
            held |= {
                path
                for path, row in self.rows.items()
                if row.target is form[0] and not is_fold(path)
            }
        return held

    # -- painting -----------------------------------------------------------------

    def _derive(self, view: dict | None, pinned: tuple[str, ...]) -> list[Fold]:
        page = self.card.page
        project = page.shell.session.project
        test = project.test(page.model_id) if project is not None else None
        subject = self.card.subject
        return [
            entry
            for entry in fold_rows(
                criteria_rows(view, subject),
                # D140: an element of the test's UnitTests component is a
                # test quantity of its own.
                unit_tests=[
                    n
                    for n in subject
                    if test is not None and is_unit_test_variable(test, n)
                ],
                pinned=pinned,
            )
            if isinstance(entry, Fold)
        ]

    def paint(self, view: dict | None) -> None:
        """The folds as the tree has them now, into the rows standing: a
        fold's row comes, goes or is repainted, and every criterion's row
        is a member of one or its own."""
        card, rows = self.card, self.rows
        if len(card.subject) < 2:
            # One variable is no family: nothing on this card can fold, and
            # most cards are this one — asked on every sync.
            return
        opened = self._open()
        pinned: tuple[str, ...] = ()
        if opened is not None and is_fold(opened) and opened in rows:
            if rows[opened].follow(view):
                # The open editor's members stay one fold whatever they
                # equal (`fold_rows`): a value on its way through another
                # group's must not merge the two under the typing hand.
                pinned = rows[opened].fold.paths
            else:
                card.page.drop_editor()
        folds = {fold.key: fold for fold in self._derive(view, pinned)}
        for key in [key for key in rows if is_fold(key) and key not in folds]:
            if self._open() == key:
                card.page.drop_editor()
            gone = rows.pop(key)
            gone.slot.delete()
            gone.row.delete()
        self.fold_of = {path: fold for fold in folds.values() for path in fold.paths}
        # A fold holding something open is unfolded, and stays so.
        unfolded = self.unfolded
        for path in self._held():
            fold = self.fold_of.get(path)
            if fold is not None and not unfolded & set(fold.paths):
                unfolded |= set(fold.paths)
                self._keep(unfolded)
        for key, fold in folds.items():
            row = rows.get(key)
            if row is None:
                row = self._add(fold)
            row.paint(fold, not unfolded & set(fold.paths))
        for path, row in rows.items():
            if is_fold(path):
                continue
            fold = self.fold_of.get(path)
            shut = fold is not None and not unfolded & set(fold.paths)
            moved = _set_prop(row.row, "data-member", "1" if fold is not None else None)
            moved |= _set_prop(row.row, "hidden", True if shut else None)
            if moved:
                row.row.update()
            if _set_prop(row.slot, "hidden", True if shut else None):
                row.slot.update()

    def _add(self, fold: Fold) -> FoldRow:
        """A fold's row and its slot, put in before its first member's row."""
        with self.column:
            row = FoldRow(self, fold, self.crumb_ch)
        first = self.rows[fold.paths[0]].row
        at = self.column.default_slot.children.index(first)
        row.row.move(target_index=at)
        row.slot.move(target_index=at + 1)
        self.rows[fold.key] = row
        return row

    # -- folding ------------------------------------------------------------------

    def toggle(self, key: str, *_: Any) -> None:
        """A chevron's click. Folding a row shut closes what is open under
        its members — an editor nobody can see is no editor, and a form
        nobody can see no form (the tree card's rule)."""
        row = self.rows.get(key)
        if row is None:
            return
        page, paths = self.card.page, set(row.fold.paths)
        unfolded = self.unfolded
        if unfolded & paths:
            unfolded -= paths
            if self._open() in paths:
                page.close_editor()
            for path in paths:
                page.drop_row(self.rows[path].target)
        else:
            unfolded |= paths
        self._keep(unfolded)
        self.paint(self.card._view())

    def unfold_to(self, path: str, view: dict | None) -> None:
        """Show ``path``'s own row: its fold, if it is in a closed one, is
        unfolded — a criterion asked for by name (a jump, a reveal, the
        tree's ⤓) is shown, not the row that folds it away."""
        fold = self.fold_of.get(path)
        unfolded = self.unfolded
        if fold is not None and not unfolded & set(fold.paths):
            self._keep(unfolded | set(fold.paths))
            self.paint(view)

    def opens(self, path: str, view: dict | None) -> bool:
        """`PlotCard.open_editor`'s first question: a fold's key opens that
        fold's editor here (True); a criterion's path has its own row shown
        first, and its editor is the card's to open."""
        row = self.rows.get(path)
        if isinstance(row, FoldRow):
            row.open_editor(view)
            return True
        self.unfold_to(path, view)
        return False

    @staticmethod
    def rekey(state: Any, model_id: str, paths: dict[str, str]) -> None:
        """An edit of the tree went through (`TestPage.edited`): each
        unfolded member is recorded where the edit put it; one it removed
        is dropped."""
        now = sorted(
            paths[path]
            for path in state.family_unfolded.get(model_id, ())
            if path in paths
        )
        if now:
            state.family_unfolded[model_id] = now
        else:
            state.family_unfolded.pop(model_id, None)
