"""The conformance test (§4.6 "Executable form"): a capability the shell has
never heard of attaches. One dummy per ``Scope``, one per ``Outcome`` kind,
and one registered through the ``dstf.plugins`` hook instead of by this
file's own call. For each the shell lists it, greys it with the reason
``available`` gave, and routes what ``act`` returned to that outcome's
effect and no other — with no file outside ``tmp_path`` written and the
repository untouched.
"""

from __future__ import annotations

import importlib.metadata
import subprocess
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402
from nicegui.testing.user_simulation import user_simulation  # noqa: E402

from dstf import plugins  # noqa: E402
from dstf.authoring import capabilities as caps  # noqa: E402
from dstf.authoring.app import Shell  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.capabilities import (  # noqa: E402
    Artifact,
    Capability,
    Job,
    OpenProject,
    Patch,
    Scope,
    StoreWrite,
)
from test_session import LOOSEN, A, B, _Gate, _project, _snapshot  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
LEAF_PATH = "/metrics/children/0"


# -- the repository is not a test fixture ---------------------------------


def _git_status() -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@pytest.fixture(scope="module")
def _repo_before() -> str:
    return _git_status()


@pytest.fixture(autouse=True)
def _repo_untouched(_repo_before: str):
    """Every test here is read-only outside its ``tmp_path``. The status is
    read once for the module and re-read after each test, so each check is
    also the next test's "before". The message names the lines that moved,
    because an editor open on the tree looks exactly like a test writing to
    it until you read them."""
    yield
    after = _git_status()
    moved = sorted(set(after.splitlines()) ^ set(_repo_before.splitlines()))
    assert after == _repo_before, f"the repository changed under the test: {moved}"


# -- the registry ---------------------------------------------------------


@pytest.fixture(autouse=True)
def _registry():
    """The builtins register on `import dstf.authoring`; each test starts
    from that state and leaves it."""
    caps._reset_for_tests()
    register_builtins()
    yield
    caps._reset_for_tests()
    register_builtins()


@pytest.fixture
def _plugin_hook():
    """`load_plugins` is idempotent per process and earlier test modules
    have already called it; forget that, before and after."""
    plugins._reset_for_tests()
    yield
    plugins._reset_for_tests()


def only(*capabilities: Capability) -> None:
    """Register these and nothing else. The claim under test is about a
    capability the shell has never heard of, and a builtin's own tab would
    make "the tab is listed" read on either of two elements."""
    caps._reset_for_tests()
    for cap in capabilities:
        caps.register_capability(cap)


@pytest.fixture
def shell(tmp_path):
    cfg, work = _project(tmp_path)
    s = Shell()
    s.open(cfg, work_dir=work)
    return s


class _Dummy:
    """A capability the shell has never heard of. Its panel is one button,
    and the button submits — the whole route from a panel to the Session."""

    def __init__(
        self,
        name: str,
        scope: Scope,
        outcome: Any = None,
        answer: bool | str = True,
    ) -> None:
        self.name = name
        self.label = f"Dummy {name}"
        self.scope = scope
        self.outcome = outcome
        self.answer = answer
        self.inputs: list[dict] = []

    def available(self, s):
        return self.answer

    def panel(self, s, panel):
        ui.button(f"submit {self.name}", on_click=lambda: panel.submit(who=self.name))

    def act(self, s, **inputs):
        self.inputs.append(inputs)
        return self.outcome


def _effects(shell: Shell, tmp_path: Path) -> dict:
    """Everything an outcome can reach, in one comparable dict: what a test
    asserts is the ONE key its outcome changed."""
    session = shell.session
    project = session.project
    return {
        "pending": [op.path for op in session.spec.pending],
        "running": session.running.label if session.running else None,
        "artifacts": [a.label for a in session.artifacts],
        "project": project.config.library_name if project else None,
        "files": _snapshot(tmp_path),
    }


def _changed(before: dict, after: dict) -> list[str]:
    return [key for key in before if before[key] != after[key]]


# -- one dummy per Scope --------------------------------------------------

SCOPES = [
    (Scope.PROJECT, {}),
    (Scope.TESTS, {"tests": {A}}),
    (Scope.VARIABLE, {"tests": {A}, "variable": "x"}),
    (Scope.LEAF, {"tests": {A}, "variable": "x", "leaf_path": LEAF_PATH}),
]


@pytest.mark.parametrize(
    ("scope", "selection"), SCOPES, ids=lambda v: getattr(v, "value", "")
)
async def test_the_shell_lists_a_capability_of_every_scope(shell, scope, selection):
    cap = _Dummy(scope.value, scope)
    only(cap)
    if selection:
        shell.session.select(**selection)
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        await user.should_see(cap.label)
        (tab,) = user.find(kind=ui.tab, content=cap.label).elements
        assert tab.enabled  # listed and reachable, not greyed
        await user.should_see(f"submit {cap.name}")  # its panel is its own


@pytest.mark.parametrize(
    ("scope", "selection"), SCOPES[1:], ids=lambda v: getattr(v, "value", "")
)
async def test_a_scope_is_not_listed_without_the_selection_it_needs(
    shell, scope, selection
):
    cap = _Dummy(scope.value, scope)
    only(cap)
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        await user.should_see("nothing applies to this selection")
        await user.should_not_see(cap.label)


async def test_the_project_less_shell_lists_only_project_scope(tmp_path):
    """§4.6: with no project open the shell shows the PROJECT-scope
    capabilities and the CLI hint."""
    project_cap = _Dummy("project", Scope.PROJECT)
    tests_cap = _Dummy("tests", Scope.TESTS)
    only(project_cap, tests_cap)
    s = Shell()
    async with user_simulation(root=s.root) as user:
        await user.open("/")
        await user.should_see("dstf author --config")
        await user.should_see(project_cap.label)
        await user.should_not_see(tests_cap.label)


# -- greyed with its reason ----------------------------------------------


async def test_an_unavailable_capability_is_greyed_with_its_reason(shell):
    """`available` returning a string is the reason, and the shell shows it
    where the capability's controls would have been — which it does not
    build, so there is nothing to submit."""
    cap = _Dummy("accept", Scope.TESTS, answer="needs a baseline")
    only(cap)
    shell.session.select(tests={B})
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        await user.should_see(cap.label)
        (tab,) = user.find(kind=ui.tab, content=cap.label).elements
        assert not tab.enabled
        await user.should_see("needs a baseline")
        with pytest.raises(AssertionError):
            user.find(f"submit {cap.name}")
        assert cap.inputs == []


# -- one dummy per Outcome kind ------------------------------------------


async def test_patch_reaches_the_pending_ops_and_nothing_else(shell, tmp_path):
    cap = _Dummy("patch", Scope.LEAF, outcome=Patch(ops=[LOOSEN]))
    only(cap)
    shell.session.select(tests={A}, variable="x", leaf_path=LEAF_PATH)
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        before = _effects(shell, tmp_path)
        user.find(f"submit {cap.name}").click()
        await user.should_see("2 pending")  # the materialization op + the edit
        after = _effects(shell, tmp_path)
    assert cap.inputs == [{"who": "patch"}] and shell.errors == {}
    assert _changed(before, after) == ["pending"]
    assert after["pending"] == ["/metrics", LOOSEN.path]


async def test_store_write_runs_against_the_store_and_nothing_else(shell, tmp_path):
    seen: list[Any] = []
    cap = _Dummy(
        "accept",
        Scope.TESTS,
        outcome=StoreWrite(run=seen.append, models=frozenset({B})),
    )
    only(cap)
    shell.session.select(tests={B})
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        before = _effects(shell, tmp_path)
        user.find(f"submit {cap.name}").click()
        await user.should_see(cap.label)  # the page survived the round trip
        after = _effects(shell, tmp_path)
    assert seen == [shell.session.project.store] and shell.errors == {}
    assert _changed(before, after) == []  # the write is the whole effect


async def test_job_runs_on_a_thread_and_the_footer_says_so(shell, tmp_path):
    gate = _Gate()
    cap = _Dummy(
        "rerun",
        Scope.TESTS,
        outcome=Job(run=gate, label="conformance rerun", models=frozenset({A})),
    )
    only(cap)
    shell.session.select(tests={A})
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        await user.should_see("no job running")
        before = _effects(shell, tmp_path)
        user.find(f"submit {cap.name}").click()
        await user.should_see("running: conformance rerun")
        assert _changed(before, _effects(shell, tmp_path)) == ["running"]
        gate.release.set()
        # The worker posts its completion; the page's timer drains it.
        await user.should_see("no job running", retries=50)
        after = _effects(shell, tmp_path)
    assert gate.seen is not None and gate.seen.models == frozenset({A})
    assert shell.errors == {}
    assert _changed(before, after) == []


async def test_artifact_is_shown_as_a_link_and_nothing_else(shell, tmp_path):
    png = tmp_path / "figure.png"
    png.write_bytes(b"\x89PNG")
    cap = _Dummy(
        "export",
        Scope.TESTS,
        outcome=Artifact(paths=[png], label="Conformance PNG"),
    )
    only(cap)
    shell.session.select(tests={A})
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        before = _effects(shell, tmp_path)
        user.find(f"submit {cap.name}").click()
        await user.should_see("Conformance PNG")
        (link,) = user.find(kind=ui.link, content="Conformance PNG").elements
        assert link.props["href"] == png.as_uri()
        after = _effects(shell, tmp_path)
    assert shell.errors == {}
    assert _changed(before, after) == ["artifacts"]
    assert after["artifacts"] == ["Conformance PNG"]


async def test_open_project_swaps_the_project_in_the_header(shell, tmp_path):
    other_cfg, other_work = _project(tmp_path / "other")
    other_cfg.write_text(
        other_cfg.read_text(encoding="utf-8").replace("FakeLib", "OtherLib", 1),
        encoding="utf-8",
    )
    cap = _Dummy(
        "recent",
        Scope.PROJECT,
        outcome=OpenProject(config_path=other_cfg, work_dir=other_work),
    )
    only(cap)
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        (name,) = user.find(marker="project").elements
        assert name.text == "FakeLib"
        before = _effects(shell, tmp_path)
        user.find(f"submit {cap.name}").click()
        await user.should_see("OtherLib")
        after = _effects(shell, tmp_path)
    assert shell.errors == {}
    assert _changed(before, after) == ["project"]
    assert shell.session.project.config.config_file == other_cfg


# -- through the plug-in hook --------------------------------------------


async def test_a_capability_registered_through_the_plugin_hook_attaches(
    shell, tmp_path, monkeypatch, _plugin_hook
):
    """Nothing here calls `register_capability`: the entry point's load()
    returns the callable that does, and `load_plugins` invokes it. A
    load_plugins that never reached the entry point fails this test."""
    cap = _Dummy("from-plugin", Scope.LEAF, outcome=Patch(ops=[LOOSEN]))

    class _EP:
        name = "fake"

        def load(self):
            return lambda: caps.register_capability(cap)

    def fake_entry_points(*, group=None):
        return [_EP()] if group == plugins.PLUGIN_GROUP else []

    monkeypatch.setattr(importlib.metadata, "entry_points", fake_entry_points)
    caps._reset_for_tests()
    plugins.load_plugins()
    assert caps.registered_capabilities() == (cap,)

    shell.session.select(tests={A}, variable="x", leaf_path=LEAF_PATH)
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        await user.should_see(cap.label)
        before = _effects(shell, tmp_path)
        user.find(f"submit {cap.name}").click()
        await user.should_see("2 pending")
        after = _effects(shell, tmp_path)
    assert shell.errors == {}
    assert _changed(before, after) == ["pending"]
