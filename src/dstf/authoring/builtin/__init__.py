"""The capabilities that ship with the tool. They register through the same
call a plug-in makes; the shell learns of them the same way."""

from __future__ import annotations


def register_builtins() -> None:
    """Idempotent, like ``comparison.builtins.register_builtins``. Nothing
    registers yet: the v1 capabilities land beside this file, and the shell
    already lists whatever is registered when a page is built."""
