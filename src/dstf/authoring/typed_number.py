"""The tool's number field: one that keeps what is typed in it.

NiceGUI resends a whole element — its ``model-value`` with it — on any write
of one of its props (a hint, a validation mark, ``update()``), and leaves the
value out only when it equals the raw string the browser last sent
(``"0.0" != "0"``, ``None != ""``). A stage that rescores takes longer than
the gap between two keys, so the answer to one keystroke landed under the
keys that followed: `0.25` typed over 0.3 read — and staged — `0.025`.

Imports ``nicegui`` at module level, as ``forms`` does: neither is on the
path of ``import dstf.authoring``, and a capability imports it inside the
function that draws.
"""

from __future__ import annotations

from typing import Any

from nicegui import ui
from nicegui.events import GenericEventArguments


class TypedNumber(ui.number, component="typed_number.js"):
    """``ui.number`` whose field shows what was typed until it is left: a
    value from the server is taken only while the field is not being typed
    in (``typed_number.js``). Every number field of the tool is one. The
    mouse wheel over a focused one scrolls the page and steps nothing: the
    field is left first (a browser steps a focused number input by 1 per
    notch).

    A field's own VALUE is still never set from the server while it is
    typed in: Discard and a released drag rebuild the editor, and a linked
    twin's mirror lands in the field that is not typed in. What the server
    does show in a field that may hold the focus is a PREVIEW
    (:meth:`preview`, D149): beside the value, never in its place.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # `ui.number` names `q-input` itself, over the registered component.
        component = type(self).component
        assert component is not None  # the class statement registered it
        self.tag = component.tag
        #: The preview the browser was last told to show, and of which drag.
        self.previewed: float | None = None
        self.preview_revision = 0

    def preview(self, value: float | None, revision: int = 0) -> None:
        """Show ``value`` in the field in place of what it holds — a handle
        held on the plot, the field following it (D149) — or, with None,
        what it holds again. The field's value does not change, so nothing
        is handed to ``on_change`` and nothing is staged. ``revision`` is
        the number of the drag the preview is of: the component shows a
        preview whatever its typing state, but not in a field typed in
        since THAT drag began (``typed_number.js``).

        A METHOD CALL on the component, not a prop: a prop write resends
        the element, and the browser then renders the page's whole tree
        again — a long task per move of the handle on an editor of thirty
        rows. ``previewed`` and ``preview_revision`` are what was last
        sent."""
        same = self.previewed == value
        if same and (value is None or self.preview_revision == revision):
            return
        self.previewed, self.preview_revision = value, revision
        self.run_method("preview", value, revision)

    def _event_args_to_value(self, e: GenericEventArguments) -> float | None:
        """What was typed, as the number it is — and a negative zero as
        zero. `-0` is on the way to every negative number and is what a
        refused `-0.5` backspaces to; a float of it is −0.0, which was
        staged as it came and written `-0.0` by Save. Here, the one place a
        typed text becomes a number for every field of the tool: a table's
        cell and a tolerance's side take their value from this."""
        value = super()._event_args_to_value(e)
        return 0.0 if value == 0 else value
