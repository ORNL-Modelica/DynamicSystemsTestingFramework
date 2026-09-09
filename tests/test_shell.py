"""The shell (§4.6 "The shell", §4.7): NiceGUI's in-process user simulation,
no browser. The shell is fixed and knows no capability by name."""

from __future__ import annotations

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402
from nicegui.testing.user_simulation import user_simulation  # noqa: E402

from dstf.authoring import capabilities as caps  # noqa: E402
from dstf.authoring.app import Shell  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from test_session import LOOSEN, A, B, C, D, _Gate, _project  # noqa: E402


@pytest.fixture(autouse=True)
def _registry():
    """The builtins register on `import dstf.authoring`; each test starts
    from that state and leaves it. (This commit ships `register_builtins`
    as an empty function; the v1 capabilities fill it.)"""
    caps._reset_for_tests()
    register_builtins()
    yield
    caps._reset_for_tests()
    register_builtins()


@pytest.fixture
def shell(tmp_path):
    cfg, work = _project(tmp_path)
    s = Shell()
    s.open(cfg, work_dir=work)
    return s


def short(model_id: str) -> str:
    return model_id.rsplit(".", 1)[-1]


async def test_header_lists_project_backend_and_pending(shell):
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        (name,) = user.find(marker="project").elements
        assert name.text == "FakeLib"  # config.library_name, not the config dir's name
        await user.should_see("backend: Python")
        await user.should_see("0 pending")
        shell.session.stage([LOOSEN])
        await user.should_see("2 pending")  # the materialization op + the edit


async def test_test_list_shows_every_test_with_its_status(shell):
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        for model in (A, B, C, D):
            assert user.find(marker=model).elements
        for status in ("FAIL", "NO_REF", "SIM_FAIL", "PASS"):
            await user.should_see(status)


async def test_filter_narrows_the_list_without_touching_the_session(tmp_path):
    cfg, work = _project(tmp_path)
    s = Shell(filter="FakeLib.A,FakeLib.D")
    s.open(cfg, work_dir=work)
    async with user_simulation(root=s.root) as user:
        await user.open("/")
        assert user.find(marker=A).elements and user.find(marker=D).elements
        with pytest.raises(AssertionError):
            user.find(marker=B)
    assert len(s.session.project.tests) == 5


async def test_selecting_a_test_shows_its_figure_and_tree(shell):
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        user.find(marker=A).click()
        await user.should_see("/metrics/children/0")  # the leaf row names its path
        plots = user.find(ui.plotly).elements
        assert plots and any(
            t.get("name") == "Reference" for t in next(iter(plots)).figure["data"]
        )
        assert shell.session.selection.tests == {A}


async def test_clicking_a_leaf_selects_it(shell):
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        user.find(marker=A).click()
        await user.should_see("/metrics/children/0")
        user.find("/metrics/children/0").click()
        assert shell.session.selection.leaf_path == "/metrics/children/0"
        assert shell.session.selection.variable == "x"


async def test_a_no_ref_test_has_the_empty_figure_and_no_tree(shell):
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        user.find(marker=B).click()
        await user.should_see("no criteria scored")
        (plot,) = user.find(ui.plotly).elements
        assert not any(t.get("name") == "Reference" for t in plot.figure["data"])


async def test_save_discard_and_export(shell):
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.stage([LOOSEN])
        user.find("Discard").click()
        await user.should_see("0 pending")
        shell.session.stage([LOOSEN])
        user.find("Export").click()
        await user.should_see("spec_patch.json")
        assert (shell.session.project.config.work_dir / "spec_patch.json").exists()
        user.find("Save").click()
        await user.should_see("0 pending")
        assert shell.session.spec.pending == []


async def test_save_refused_on_a_changed_file_offers_reload(shell):
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.stage([LOOSEN])
        p = shell.session.spec.path
        p.write_text(p.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        user.find("Save").click()
        await user.should_see("changed on disk")
        user.find("Reload").click()
        await user.should_see("0 pending")


async def test_a_thread_posted_completion_reaches_the_page(shell):
    """The bridge: Session(post=shell.post) queues the worker's completion;
    the page's timer drains it inside the page's context."""
    from dstf.authoring.capabilities import Job

    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        gate = _Gate()
        shell.session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
        await user.should_see("running: rerun")
        gate.release.set()
        await user.should_see("no job running")  # the drained completion cleared it


async def test_the_pending_drawer_lists_staged_ops_by_model(shell):
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.stage([LOOSEN])
        # The `spec` event's refresh is deferred; the button carries its new
        # label only after it lands.
        await user.should_see("2 pending")
        user.find("2 pending").click()
        await user.should_see("/metrics/children/0/tolerance")
        await user.should_see(A)


async def test_an_artifact_is_shown_as_a_link(shell):
    from pathlib import Path

    from dstf.authoring.capabilities import Artifact

    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.apply(Artifact(paths=[Path("/tmp/x.png")], label="PNG export"))
        await user.should_see("PNG export")


async def test_project_less_state_shows_the_hint_and_only_project_capabilities(
    tmp_path,
):
    s = Shell()
    async with user_simulation(root=s.root) as user:
        await user.open("/")
        await user.should_see("dstf author --config")
        await user.should_not_see("Edit criteria")


async def test_close_project_reaches_the_project_less_state(shell):
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        user.find("Close project").click()
        await user.should_see("dstf author --config")
        assert shell.session.project is None
