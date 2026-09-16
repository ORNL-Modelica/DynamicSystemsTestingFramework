"""The conformance test (§4.6 "Executable form", amended by D129): a
capability the shell has never heard of attaches at each placement — one
dummy per `Placement`, one per `Outcome` kind, and one registered through
the `dstf.plugins` hook instead of by this file's own call. The shell lists
it with its caption, greys it with the sentence `available` gave, and
routes what `act` returned to that outcome's effect and no other — with no
file outside `tmp_path` written and the repository untouched. SUITE and
TEST are drawn by the two pages this sub-project has; VARIABLE and
CRITERION are listed by the shell's `placed` query and drawn by the Test
page from sub-project 3 (owner call 2 of the plan)."""

from __future__ import annotations

import asyncio
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
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.capabilities import (  # noqa: E402
    Artifact,
    Job,
    OpenProject,
    Patch,
    Placement,
    StoreWrite,
)
from dstf.authoring.session import Selection  # noqa: E402
from dstf.authoring.shell.shell import Shell  # noqa: E402
from dstf.authoring.shell.suite import SuitePage  # noqa: E402
from test_session import LOOSEN, A, B, _Gate, _project, _snapshot  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


# -- the repository is not a test fixture ---------------------------------


def _git_status() -> str:
    """The tree's status, or "" where there is no repository — a `git
    archive` copy, which is where this file's fresh-context reviews run it
    (the check that the repository is untouched has nothing to guard there)."""
    if not (REPO / ".git").exists():
        return ""
    return subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,  # any other git failure is a real one
    ).stdout


@pytest.fixture(scope="module")
def _repo_before() -> str:
    return _git_status()


@pytest.fixture(scope="module", autouse=True)
def _repo_untouched(_repo_before: str):
    """Every test here is read-only outside its ``tmp_path``. The status is
    read once before the module and once after (a `git status` on this
    tree costs a second and a half; per test it doubled the module)."""
    yield
    after = _git_status()
    moved = sorted(set(after.splitlines()) ^ set(_repo_before.splitlines()))
    assert after == _repo_before, f"the repository changed under the test: {moved}"


# -- the registry ---------------------------------------------------------


@pytest.fixture(autouse=True)
def _registry():
    caps._reset_for_tests()
    register_builtins()
    yield
    caps._reset_for_tests()
    register_builtins()


@pytest.fixture
def _plugin_hook():
    plugins._reset_for_tests()
    yield
    plugins._reset_for_tests()


def only(*capabilities: Any) -> None:
    """Register these and nothing else: the claim under test is about a
    capability the shell has never heard of, and a builtin's own button
    would make "a button is drawn" read on either of two elements."""
    caps._reset_for_tests()
    for cap in capabilities:
        caps.register_capability(cap)


@pytest.fixture
def shell(tmp_path):
    cfg, work = _project(tmp_path)
    s = Shell(cfg, work_dir=work)
    s.session.open(cfg, work_dir=work)
    s.load.phase = "done"
    return s


class _Dummy:
    """A capability the shell has never heard of. Its panel is one button,
    and the button submits — the whole route from a panel to the Session."""

    def __init__(
        self,
        name: str,
        placements: set[Placement],
        outcome: Any = None,
        answer: bool | str = True,
    ) -> None:
        self.name = name
        self.label = f"Dummy {name}"
        self.placements = frozenset(placements)
        self.outcome = outcome
        self.answer = answer
        self.inputs: list[dict] = []

    def available(self, s):
        return self.answer

    def caption(self, s):
        return f"Dummy {self.name} on {len(s.selection.tests)} tests"

    def panel(self, s, panel):
        ui.button(f"submit {self.name}", on_click=lambda: panel.submit(who=self.name))

    def act(self, s, **inputs):
        self.inputs.append(inputs)
        return self.outcome


async def _settled(predicate, tries: int = 60) -> None:
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0.05)
    assert predicate()


def _one(user, marker: str):
    (element,) = user.find(marker=marker).elements
    return element


async def _open(user, path: str = "/") -> None:
    """Open the page AND wait for it to be built: every page awaits its
    client's handshake before drawing, and `user.open` returns as soon as
    the handshake is sent — the page resumes on the next loop iteration.
    The header's wordmark is the first element every page draws."""
    await user.open(path)
    await user.should_see(marker="wordmark", retries=20)


def _suite(shell, user) -> SuitePage:
    """The Suite page built for the user's current client (a navigated-away
    page stays registered under the simulation)."""
    (page,) = [
        p for p in shell.pages if isinstance(p, SuitePage) and p.client is user.client
    ]
    return page


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


async def _check(user, model_id: str) -> None:
    user.find(marker=f"check:{model_id}").click()


async def _submit(user, cap: _Dummy) -> None:
    """The caption button opens the dialog; the panel's own button submits."""
    user.find(marker=f"action:{cap.name}").click()
    await user.should_see(f"submit {cap.name}", retries=20)
    user.find(f"submit {cap.name}").click()


# -- one dummy per Placement ------------------------------------------------


@pytest.mark.parametrize("placement", list(Placement), ids=lambda p: p.value)
def test_the_shell_lists_a_capability_at_its_placement_and_nowhere_else(
    shell, placement
):
    cap = _Dummy(placement.value, {placement})
    only(cap)
    view = shell.view_for(Selection(tests=frozenset({A})))
    assert shell.placed(placement, view) == [(cap, True)]
    for other in Placement:
        if other is not placement:
            assert shell.placed(other, view) == []


async def test_the_suite_page_draws_a_suite_capability_with_its_caption(shell):
    suite_cap = _Dummy("suite", {Placement.SUITE})
    test_cap = _Dummy("test", {Placement.TEST})
    only(suite_cap, test_cap)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        button = _one(user, "action:suite")
        assert button.text == "Dummy suite on 0 tests" and button.enabled
        await user.should_not_see(marker="action:test")  # not this page's
        await _check(user, A)
        await _settled(lambda: button.text == "Dummy suite on 1 tests")


async def test_the_test_page_draws_a_test_capability_for_its_one_test(shell):
    suite_cap = _Dummy("suite", {Placement.SUITE})
    test_cap = _Dummy("test", {Placement.TEST})
    only(suite_cap, test_cap)
    async with user_simulation(root=shell.index) as user:
        shell.mount()  # the Test page's route, without visiting `/` first
        await _open(user, f"/test/{A}")
        button = _one(user, "action:test")
        assert button.text == "Dummy test on 1 tests" and button.enabled
        await user.should_not_see(marker="action:suite")


# -- greyed with its reason ------------------------------------------------


async def test_an_unavailable_capability_is_greyed_with_its_reason(shell):
    """`available` returning a string is the sentence beside the button;
    the panel is not built, so there is nothing to submit."""
    cap = _Dummy("accept", {Placement.SUITE}, answer="needs a baseline")
    only(cap)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        button = _one(user, "action:accept")
        assert not button.enabled and button.text == "Dummy accept on 0 tests"
        assert _one(user, "action:accept:why").text == "needs a baseline"
        with pytest.raises(AssertionError):  # `find` raises on absence
            user.find("submit accept")
        assert cap.inputs == []


async def test_a_capability_whose_available_or_caption_raises_is_greyed_not_fatal(
    shell, caplog
):
    """The bar reads every registered capability's `available` and `caption`
    on every refresh, so a plug-in that raises there would abort the page's
    build — the Suite page came up blank (the Task 7 review's probe). Like
    `panel`, they never break the page: the button is greyed with the
    exception, and a raising `caption` falls back to the label."""

    class _Broken(_Dummy):
        def available(self, s):
            raise KeyError("not for this view")

    class _Mute(_Dummy):
        def caption(self, s):
            raise ValueError("no words")

    broken = _Broken("broken", {Placement.SUITE})
    mute = _Mute("mute", {Placement.SUITE})
    caps.register_capability(broken)
    caps.register_capability(mute)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        button = _one(user, "action:broken")
        assert not button.enabled and button.text == "Dummy broken on 0 tests"
        assert _one(user, "action:broken:why").text == "KeyError: 'not for this view'"
        mute_button = _one(user, "action:mute")
        assert mute_button.text == "Dummy mute" and mute_button.enabled
        assert shell.pages  # the page was built and registered
        # Logged with the traceback once per capability and method, not per
        # refresh — the bar refreshes on every event and every checkbox.
        _suite(shell, user).actions.refresh()
        _suite(shell, user).actions.refresh()
        reports = [r for r in caplog.records if r.message.startswith("capability ")]
        assert [r.message[:38] for r in reports] == [
            "capability 'broken': available raised ",
            "capability 'mute': caption raised Valu",
        ]
        assert all(r.exc_info for r in reports)


async def test_batch_actions_are_greyed_while_the_project_loads(shell):
    """Brief §3.8: batch actions unlock when loading finishes."""
    cap = _Dummy("suite", {Placement.SUITE})
    only(cap)
    # The fixture's synchronous open left its events queued; the shell's
    # FIRST tick (`immediate=True`) replays them — scan, spec, results, done
    # — while the page waits for its handshake, and `_absorb` would reset the
    # phase this test sets. Drain them: this test is about the in-between.
    while not shell._events.empty():
        shell._events.get_nowait()
    shell.load.phase = "results"  # as during an open
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        button = _one(user, "action:suite")
        assert not button.enabled
        assert (
            _one(user, "action:suite:why").text
            == "batch actions unlock when loading finishes"
        )
        shell.load.phase = "done"
        shell._events.put({"event": "loading", "phase": "done"})
        await _settled(lambda: button.enabled)


# -- one dummy per Outcome kind ------------------------------------------


async def test_patch_reaches_the_pending_ops_and_nothing_else(shell, tmp_path):
    cap = _Dummy("patch", {Placement.SUITE}, outcome=Patch(ops=[LOOSEN]))
    only(cap)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        await _check(user, A)
        before = _effects(shell, tmp_path)
        await _submit(user, cap)
        await _settled(lambda: _one(user, "pending").text == "2 pending edits")
        after = _effects(shell, tmp_path)
    assert cap.inputs == [{"who": "patch"}] and _suite(shell, user).actions.errors == {}
    assert _changed(before, after) == ["pending"]
    assert after["pending"] == ["/metrics", LOOSEN.path]


async def test_store_write_runs_against_the_store_and_nothing_else(shell, tmp_path):
    seen: list[Any] = []
    cap = _Dummy(
        "accept",
        {Placement.SUITE},
        outcome=StoreWrite(run=seen.append, models=frozenset({B})),
    )
    only(cap)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        await _check(user, B)
        before = _effects(shell, tmp_path)
        await _submit(user, cap)
        await _settled(lambda: seen)
        after = _effects(shell, tmp_path)
    assert (
        seen == [shell.session.project.store]
        and _suite(shell, user).actions.errors == {}
    )
    assert _changed(before, after) == []  # the write is the whole effect


async def test_job_runs_on_a_thread_and_the_runs_card_says_so(shell, tmp_path):
    gate = _Gate()
    cap = _Dummy(
        "rerun",
        {Placement.SUITE},
        outcome=Job(run=gate, label="conformance rerun", models=frozenset({A})),
    )
    only(cap)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        await user.should_see("no job running")
        await _check(user, A)
        before = _effects(shell, tmp_path)
        await _submit(user, cap)
        await user.should_see("running: conformance rerun", retries=20)
        assert _changed(before, _effects(shell, tmp_path)) == ["running"]
        gate.release.set()
        await user.should_see("no job running", retries=50)
        after = _effects(shell, tmp_path)
    assert gate.seen is not None and gate.seen.models == frozenset({A})
    assert _suite(shell, user).actions.errors == {}
    assert _changed(before, after) == []


async def test_artifact_is_shown_as_a_link_and_nothing_else(shell, tmp_path):
    png = tmp_path / "figure.png"
    png.write_bytes(b"\x89PNG")
    cap = _Dummy(
        "export",
        {Placement.SUITE},
        outcome=Artifact(paths=[png], label="Conformance PNG"),
    )
    only(cap)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        before = _effects(shell, tmp_path)
        await _submit(user, cap)
        await user.should_see("Conformance PNG", retries=20)
        (link,) = user.find(kind=ui.link, content="Conformance PNG").elements
        assert link.props["href"] == png.as_uri()
        after = _effects(shell, tmp_path)
    assert _suite(shell, user).actions.errors == {}
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
        {Placement.SUITE},
        outcome=OpenProject(config_path=other_cfg, work_dir=other_work),
    )
    only(cap)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        assert _one(user, "project").text == "FakeLib"
        before = _effects(shell, tmp_path)
        await _submit(user, cap)
        await _settled(lambda: _one(user, "project").text == "OtherLib")
        after = _effects(shell, tmp_path)
    assert _suite(shell, user).actions.errors == {}
    assert _changed(before, after) == ["project"]
    assert shell.session.project.config.config_file == other_cfg


async def test_an_act_that_raises_shows_in_the_dialog_and_changes_nothing(
    shell, tmp_path
):
    class _Raising(_Dummy):
        def act(self, s, **inputs):
            raise ValueError("nope")

    cap = _Raising("bad", {Placement.SUITE})
    only(cap)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        before = _effects(shell, tmp_path)
        await _submit(user, cap)
        await user.should_see("ValueError: nope", retries=20)
        assert _suite(shell, user).actions.errors == {"bad": "ValueError: nope"}
        assert _one(user, "error:bad").visible
        assert _changed(before, _effects(shell, tmp_path)) == []


# -- through the plug-in hook --------------------------------------------


async def test_a_capability_registered_through_the_plugin_hook_attaches(
    shell, tmp_path, monkeypatch, _plugin_hook
):
    """Nothing here calls `register_capability`: the entry point's load()
    returns the callable that does, and `load_plugins` invokes it."""
    cap = _Dummy("from-plugin", {Placement.SUITE}, outcome=Patch(ops=[LOOSEN]))

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

    async with user_simulation(root=shell.index) as user:
        await _open(user)
        await _check(user, A)
        before = _effects(shell, tmp_path)
        await _submit(user, cap)
        await _settled(lambda: _one(user, "pending").text == "2 pending edits")
        after = _effects(shell, tmp_path)
    assert _suite(shell, user).actions.errors == {}
    assert _changed(before, after) == ["pending"]
