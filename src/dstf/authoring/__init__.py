"""The authoring tier's core — the Session, its spec document and the
capability contract (two-tier design, §4.5–4.6). Pure Python: no UI
framework is imported here; sub-project 4's shell sits on top.

Importing the package registers the capabilities it ships (the shell then
lists whatever is registered); the capability modules reach NiceGUI only
inside their ``panel``, so this import works without the ``[author]``
extra.
"""

from .builtin import register_builtins

register_builtins()
