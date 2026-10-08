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

    There is no way to set a FOCUSED field from the server, because nothing
    does: Discard and a drag on the plot rebuild the editor, and a linked
    twin's mirror lands in the field that is not typed in. A feature that
    does replace the value of a field being typed in has to add that — a
    revision prop the component watches, bumped beside the value, on which
    it takes the server's value whatever its typing state.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # `ui.number` names `q-input` itself, over the registered component.
        component = type(self).component
        assert component is not None  # the class statement registered it
        self.tag = component.tag

    def _event_args_to_value(self, e: GenericEventArguments) -> float | None:
        """What was typed, as the number it is — and a negative zero as
        zero. `-0` is on the way to every negative number and is what a
        refused `-0.5` backspaces to; a float of it is −0.0, which was
        staged as it came and written `-0.0` by Save. Here, the one place a
        typed text becomes a number for every field of the tool: a table's
        cell and a tolerance's side take their value from this."""
        value = super()._event_args_to_value(e)
        return 0.0 if value == 0 else value
