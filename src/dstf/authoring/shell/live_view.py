"""The Test page's part of a running test's view (D151): once a second —
the shell's `poll` — it says which variables its DRAWN plots show, and
re-sends the plots the last read brought samples for. It reads no file:
`Session.live_runs` polls once for every page, and the figure a card sends
is the Session's, the Actual trace the samples read so far.

A plot that is not drawn asks for nothing — a card draws when it scrolls
into view — and one Focus hides asks for nothing either. A re-send is the
card's own `redraw` (`Plotly.react`): a zoom, the legend's choices and the
page's scroll position are the browser's, and stay.

It also marks the page while its test has nothing scored because of a run
— in progress, or ended without finishing: no criterion has a verdict
then, and no pill is drawn (the rows say `waiting for result` or `not
scored` where a readout goes)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .tree_card import _set_prop

if TYPE_CHECKING:
    from .test_page import TestPage

logger = logging.getLogger(__name__)

LIVE_CSS = """
/* A test with nothing scored because of a run (D151): its criteria have no
   verdict, so their pills are not drawn — the tree's, the plots' and the
   Statistics card's. The header's badge is the test's own word. */
.dstf-test[data-unscored] .dstf-pill { display: none; }
/* One line under the strip: why a running test's page shows no growing
   trace. */
.dstf-strip-note { padding: 2px 24px; font-size: 12px; color: var(--dstf-text-muted);
  border-top: 1px solid var(--dstf-border-hairline); }
"""


class LiveView:
    def __init__(self, page: TestPage) -> None:
        self.page = page

    def mark(self, view: dict | None) -> None:
        """The page's body says the word its criteria say in place of a
        verdict (`live_rows.unscored_view`), or nothing. An attribute, the
        server's own: a class the browser set would not survive a repaint."""
        body = self.page.body
        if body is None:
            return
        word = None if view is None else view.get("unscored")
        if _set_prop(body, "data-unscored", word):
            body.update()

    def names(self) -> set[str]:
        """The variables of the plots that are drawn and shown."""
        return {
            name
            for card in self._drawn()
            for name in card.resolved.series
            if name not in card.resolved.missing
        }

    def _drawn(self) -> list:
        return [
            card
            for card in self.page.cards
            if card.plot is not None and card.card.visible
        ]

    def tick(self) -> None:
        """Ask for the drawn plots' variables, and re-send each plot the
        last poll brought a sample for. Nothing on a test that is not
        running, or runs on a backend without a live view."""
        page = self.page
        runs, model_id = page.shell.session.live_runs, page.model_id
        if not runs.polled(model_id):
            return
        runs.ask(model_id, self, self.names())
        moved = [
            card
            for card in self._drawn()
            if any(runs.moved(model_id, name) for name in card.resolved.series)
        ]
        if not moved:
            return
        view = page.shell.session.tree_view(model_id)  # one read for them all
        for card in moved:
            try:
                card.redraw(view)
            except Exception:  # one card's failure is not another's
                logger.exception("live plot %s of %s", card.index, model_id)
