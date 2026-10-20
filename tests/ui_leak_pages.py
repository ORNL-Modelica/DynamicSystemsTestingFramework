"""Not a test module (no ``test_`` prefix): what `test_ui_leak.py` runs in
a pytest of its own — several tests in a row, each opening the Test page
on a Shell of its own as the UI tests do, and a last one that counts the
Shells still alive. Run alone it is one process's sequence, whatever
distributes the suite around it."""

from __future__ import annotations

import gc

import pytest

pytest.importorskip("nicegui")

from nicegui.testing.user_simulation import user_simulation  # noqa: E402

from dstf.authoring.shell.shell import Shell  # noqa: E402
from test_session import A, _two_var_project  # noqa: E402

PAGES = 6
#: NiceGUI keeps the last simulation's root function (`core.root`) past its
#: exit, and with it the last page test's Shell: one stays (measured), one
#: more is margin. A leak keeps every test's, `PAGES` of them.
MOST = 2


@pytest.mark.parametrize("i", range(PAGES))
async def test_a_test_page_opened_on_a_shell_of_its_own(tmp_path, i):
    cfg, work = _two_var_project(tmp_path)
    shell = Shell(cfg, work_dir=work)
    shell.session.open(cfg, work_dir=work)
    shell.load.phase = "done"
    async with user_simulation(root=shell.index) as user:
        shell.mount()
        await user.open(f"/test/{A}")
        await user.should_see(marker="wordmark", retries=20)


def test_the_shells_of_the_tests_before_are_gone():
    gc.collect()
    alive = sum(1 for o in gc.get_objects() if type(o) is Shell)
    assert alive <= MOST, f"{alive} Shells alive after {PAGES} Test pages"
