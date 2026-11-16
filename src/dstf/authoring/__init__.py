"""The authoring tier's core — the Session, its spec document and the
capability contract. Pure Python: no UI framework is imported here; the
NiceGUI shell (``authoring.shell``) sits on top.

Importing the package registers the capabilities it ships (the shell then
lists whatever is registered); the capability modules reach NiceGUI only
inside their ``panel``, so this import works without the ``[author]``
extra.
"""

from .builtin import register_builtins

register_builtins()
