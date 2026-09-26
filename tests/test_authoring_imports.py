"""Sub-project 1's predicate 5, amended by sub-project 2: every module under
`dstf.authoring` outside `shell/`, `forms.py` and `builtin/` imports no UI
framework. The Session and the contract must import without the `[author]`
extra; the check runs them in a subprocess where `nicegui` cannot import."""

import subprocess
import sys

PROBE = """
import builtins, sys
real = builtins.__import__
def no_ui(name, *a, **k):
    if name == "nicegui" or name.startswith("nicegui."):
        raise ImportError("nicegui is not installed here")
    if name == "regex":  # the drawer's regex filter, also of the extra
        raise ImportError("regex is not installed here")
    return real(name, *a, **k)
builtins.__import__ = no_ui
import dstf.authoring  # registers the builtins, imports the Session and the contract
import dstf.authoring.session, dstf.authoring.capabilities, dstf.authoring.spec_document
import dstf.authoring.tree_pointer, dstf.authoring.layout, dstf.authoring.variable_rows
import dstf.authoring.builtin.accept, dstf.authoring.builtin.rerun
import dstf.authoring.builtin.edit_criteria, dstf.authoring.builtin.edit_tree
import dstf.authoring.builtin.edit_on_plot
assert not any(m == "nicegui" or m.startswith("nicegui.") for m in sys.modules), "nicegui was imported"
print("ok")
"""


def test_the_session_tier_imports_without_a_ui_framework():
    done = subprocess.run(
        [sys.executable, "-c", PROBE], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok"


def test_the_shell_package_is_the_ui_tier():
    """The inverse: the shell package DOES import NiceGUI, and nothing
    else under `authoring/` but `forms.py` and `builtin/` may."""
    import pathlib

    import dstf.authoring

    root = pathlib.Path(dstf.authoring.__file__).parent
    offenders = sorted(
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if "nicegui" in p.read_text(encoding="utf-8")
        and p.parts[len(root.parts)] not in ("shell", "builtin", "forms.py")
    )
    assert offenders == []
