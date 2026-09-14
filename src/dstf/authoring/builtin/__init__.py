"""The capabilities that ship with the tool. They register through the same
call a plug-in makes; the shell learns of them the same way."""

from __future__ import annotations

from ..capabilities import register_capability, registered_capabilities


def register_builtins() -> None:
    """Idempotent, like ``comparison.builtins.register_builtins``."""
    names = {c.name for c in registered_capabilities()}
    from .accept import Accept
    from .edit_criteria import EditCriteria
    from .edit_tree import EditTree

    if EditCriteria.name not in names:
        register_capability(EditCriteria())
    if EditTree.name not in names:
        register_capability(EditTree())
    if Accept.name not in names:
        register_capability(Accept())
