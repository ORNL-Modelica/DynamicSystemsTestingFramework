"""The Test page's status strip (the owner's ruling Q2 A): a full-width
row directly under the test's row, inside the header that stays at the
top. It says the test's situation (`authoring.situation`) in a tone, and
fills the one next action: the shell's own Preview and save / Discard for
pending edits (quiet when the edits name a variable only a new run
records), its own Remove or Cancel for a test the simulation queue holds
(D150), and the TEST-placement capabilities, each drawn as its
declared `emphasis` for the situation says — filled, quiet, or in the ⋮.
The strip knows no capability by name."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from nicegui import ui

from ...backends.pool import ItemState
from ..capabilities import Placement
from ..errors import user_text
from ..session import Selection
from ..situation import Situation, Strip
from .actions import ActionBar, log_unless_users
from .widgets import Action

if TYPE_CHECKING:
    from ...backends.pool import QueueItem
    from .test_page import TestPage

TONES = ("grey", "red", "amber")
DISCARD_TIP = "Discards this test's edits; the header's Discard drops every test's."


class StatusStrip:
    def __init__(self, page: TestPage) -> None:
        self.page = page
        #: What the strip says now; the bar's emphasis reads it. None while
        #: the page has no test (`forget`).
        self.situation: Situation | None = None
        with (
            ui.row()
            .classes("dstf-strip dstf-strip-grey items-center gap-3 w-full")
            .mark("strip") as self.row
        ):
            self.text = ui.label().classes("dstf-strip-text").mark("strip-text")
            # The sentence in full where it is one line of more (a failed
            # run's message): on hover, and for a screen reader.
            with self.text:
                self.tip = ui.tooltip("").mark("strip-text:tip")
            self.tip.set_visibility(False)
            with ui.row().classes("dstf-strip-actions items-center gap-2"):
                self.preview = (
                    ui.button("Preview and save", on_click=self._preview)
                    .props("unelevated dense no-caps color=primary")
                    .mark("strip-preview")
                )
                self.discard = Action(
                    self._discard,
                    props="flat dense no-caps",
                    marker="strip-discard",
                    why=False,
                )
                # The board's label, and the header's: the tooltip says
                # which edits this one drops.
                with self.discard.button:
                    ui.tooltip(DISCARD_TIP)
                # The queue's two: a queued test's next action is Remove, a
                # running one's Cancel — on a backend that cannot stop a
                # test, the sentence saying so in its place.
                self.remove = (
                    ui.button("Remove", on_click=self._remove)
                    .props("unelevated dense no-caps color=primary")
                    .mark("strip-remove")
                )
                self.cancel = (
                    ui.button("Cancel", on_click=self._cancel)
                    .props("unelevated dense no-caps color=negative")
                    .mark("strip-cancel")
                )
                self.cancel_why = (
                    ui.label().classes("dstf-why").mark("strip-cancel:why")
                )
                self.actions = ActionBar(
                    page.shell,
                    Placement.TEST,
                    lambda: Selection(tests=frozenset({page.model_id})),
                    marker="test-actions",
                    apply=page.apply,
                    emphasis=lambda cap: getattr(cap, "emphasis", {}).get(
                        self.situation
                    ),
                )
        #: One line under the strip (`Strip.note`): why a running test's
        #: page shows no growing trace (D151). Hidden while there is none.
        self.note = ui.label().classes("dstf-strip-note w-full").mark("strip-note")
        self.note.set_visibility(False)
        self._show_edit_buttons(False)
        self._show_queue_buttons(None)

    def _show_edit_buttons(self, editing: bool, *, filled: bool = True) -> None:
        """Preview and save and Discard, shown while edits are pending;
        Preview and save is the filled next action unless the edits name a
        variable only a new run records — Queue is then the one filled (the
        owner's O1), and Preview and save is quiet, still one click."""
        self.preview.set_visibility(editing)
        self.discard.container.set_visibility(editing)
        if filled:
            self.preview.props(remove="flat", add="unelevated color=primary")
        else:
            self.preview.props(remove="unelevated color=primary", add="flat")

    def _tone(self, tone: str) -> None:
        self.row.classes(
            remove=" ".join(f"dstf-strip-{t}" for t in TONES),
            add=f"dstf-strip-{tone}",
        )

    def paint(self, strip: Strip) -> None:
        self._tone(strip.tone)
        self.text.set_text(strip.sentence)
        self.note.set_text(strip.note)
        self.note.set_visibility(bool(strip.note))
        self.tip.set_text(strip.tip)
        self.tip.set_visibility(bool(strip.tip))
        # Before the bar's refresh: its emphasis reads the situation.
        self.situation = strip.situation
        self._show_edit_buttons(
            strip.situation in (Situation.EDITS, Situation.EDITS_NEW),
            filled=strip.situation is not Situation.EDITS_NEW,
        )
        self.discard.set("Discard", self.page.shell.busy_reason())
        self._show_queue_buttons(strip.situation)
        self.actions.container.set_visibility(True)
        self.actions.refresh()

    def say(self, strip: Strip) -> None:
        """The sentence alone: a running test's seconds — or how far its
        result file has got — move once a second, and nothing else of the
        strip does."""
        self.text.set_text(strip.sentence)

    def _show_queue_buttons(self, situation: Situation | None) -> None:
        session = self.page.shell.session
        why = session.cannot_cancel
        running = situation is Situation.RUNNING
        # A stop already asked of it: no second Cancel while it ends.
        item = session.queue_item(self.page.model_id)
        stopping = item is not None and item.stopping
        # Queued, and the queue holds its item: a test whose click is
        # still being read (`Session.checking`) has nothing to remove yet.
        self.remove.set_visibility(
            situation is Situation.QUEUED
            and item is not None
            and session.item_state(item) is ItemState.WAITING
        )
        self.cancel.set_visibility(running and why is None and not stopping)
        self.cancel_why.set_text(why or "")
        self.cancel_why.set_visibility(running and why is not None)

    def forget(self) -> None:
        """No test: no sentence, no buttons (the page's `_gone`)."""
        self.situation = None
        self.text.set_text("")
        self.note.set_visibility(False)
        self.tip.set_visibility(False)
        self._tone("grey")
        self._show_edit_buttons(False)
        self._show_queue_buttons(None)
        self.actions.clear()
        self.actions.container.set_visibility(False)

    def _preview(self) -> None:
        self.page.header.drawer.show(self.page.model_id)

    def _discard(self) -> None:
        # THIS test's edits; the header's Discard is every test's.
        try:
            self.page.shell.session.discard(models={self.page.model_id})
        except Exception as e:
            log_unless_users(e, "discard")
            ui.notify(user_text(e), type="warning")

    def _remove(self) -> None:
        self._queue_act(self.page.shell.session.remove, "has already started")

    def _cancel(self) -> None:
        self._queue_act(self.page.shell.session.cancel, "is no longer running")

    def _queue_act(self, act: Callable[[QueueItem], bool], missed: str) -> None:
        """Remove or Cancel this test's item; a refusal — the pool's, or the
        item having moved on since the strip was painted — is said."""
        session, model_id = self.page.shell.session, self.page.model_id
        item = session.queue_item(model_id)
        try:
            done = item is not None and act(item)
        except Exception as e:
            log_unless_users(e, "queue")
            ui.notify(user_text(e), type="warning")
            return
        if not done:
            ui.notify(f"{model_id} {missed}", type="warning")
