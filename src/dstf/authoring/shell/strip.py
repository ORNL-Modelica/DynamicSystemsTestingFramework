"""The Test page's status strip (the owner's ruling Q2 A): a full-width
row directly under the test's row, inside the header that stays at the
top. It says the test's situation (`authoring.situation`) in a tone, and
fills the one next action: the shell's own Preview and save / Discard for
pending edits (quiet when the edits name a variable only a rerun records),
and the TEST-placement capabilities, each drawn as its
declared `emphasis` for the situation says — filled, quiet, or in the ⋮.
The strip knows no capability by name."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

from ..capabilities import Placement
from ..errors import user_text
from ..session import Selection
from ..situation import Situation, Strip
from .actions import ActionBar, log_unless_users
from .widgets import Action

if TYPE_CHECKING:
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
        self._show_edit_buttons(False)

    def _show_edit_buttons(self, editing: bool, *, filled: bool = True) -> None:
        """Preview and save and Discard, shown while edits are pending;
        Preview and save is the filled next action unless the edits name a
        variable only a rerun records — Rerun is then the one filled (the
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
        # Before the bar's refresh: its emphasis reads the situation.
        self.situation = strip.situation
        self._show_edit_buttons(
            strip.situation in (Situation.EDITS, Situation.EDITS_NEW),
            filled=strip.situation is not Situation.EDITS_NEW,
        )
        self.discard.set("Discard", self.page.shell.busy_reason())
        self.actions.container.set_visibility(True)
        self.actions.refresh()

    def forget(self) -> None:
        """No test: no sentence, no buttons (the page's `_gone`)."""
        self.situation = None
        self.text.set_text("")
        self._tone("grey")
        self._show_edit_buttons(False)
        self.actions.clear()
        self.actions.container.set_visibility(False)

    def _preview(self) -> None:
        self.page.header.drawer.show(self.page.model_id)

    def _discard(self) -> None:
        # THIS test's edits; the header's Discard is every test's. Refused
        # while a job runs, as the header's is: the button greys on the
        # next tick, and a click within the one the job started in still
        # reaches the Session.
        try:
            self.page.shell.session.discard(models={self.page.model_id})
        except Exception as e:
            log_unless_users(e, "discard")
            ui.notify(user_text(e), type="warning")
