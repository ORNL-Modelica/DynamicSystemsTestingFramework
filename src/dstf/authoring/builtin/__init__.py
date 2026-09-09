"""The capabilities that ship with the tool. They register through the same
call a plug-in makes; the shell learns of them the same way."""

from __future__ import annotations

from ..capabilities import register_capability, registered_capabilities


def register_builtins() -> None:
    """Idempotent, like ``comparison.builtins.register_builtins``."""
    names = {c.name for c in registered_capabilities()}
    from .edit_criteria import EditCriteria

    if EditCriteria.name not in names:
        register_capability(EditCriteria())
