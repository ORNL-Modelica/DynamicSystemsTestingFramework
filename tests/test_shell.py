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
        await user.should_see("2 pending")  # the staged state must have landed first
        user.find("Discard").click()
        await user.should_see("0 pending")
        assert shell.session.spec.pending == []
        shell.session.stage([LOOSEN])
        await user.should_see("2 pending")
        user.find("Export").click()
        await user.should_see("spec_patch.json")
        assert (shell.session.project.config.work_dir / "spec_patch.json").exists()
        user.find("Save").click()
        await user.should_see("0 pending")
        assert shell.session.spec.pending == []
        assert (
            "metrics" in shell.session.spec.loaded["tests"][0]
        )  # saved, not discarded


async def test_save_refused_on_a_changed_file_offers_reload(shell):
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.stage([LOOSEN])
        await user.should_see("2 pending")
        p = shell.session.spec.path
        p.write_text(p.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        user.find("Save").click()
        await user.should_see("changed on disk")
        assert len(shell.session.spec.pending) == 2  # refused, kept
        user.find("Reload").click()
        await user.should_see("0 pending")
        assert (
            shell.session.spec.pending == []
            and not shell.session.spec.on_disk_changed()
        )


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
        assert shell._drawer_el.value is False
        user.find("2 pending").click()
        assert shell._drawer_el.value is True  # the button opens the drawer
        await user.should_see("/metrics/children/0/tolerance")
        # The model name as a LABEL inside the drawer: the test-list row is
        # a button marked with the id, so this proves the grouping.
        labels = list(user.find(kind=ui.label, content=A).elements)
        assert labels and all(_inside(e, shell._drawer_el) for e in labels)


def _inside(element, container) -> bool:
    node = element
    while node is not None:
        if node is container:
            return True
        node = node.parent_slot.parent if node.parent_slot is not None else None
    return False


async def test_the_dashboard_route_serves_the_open_projects_work_dir(shell):
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        work = shell.session.project.config.work_dir
        (work / "dashboard.html").write_text("<p>dash</p>", encoding="utf-8")
        ok = await user.http_client.get("/dashboard/dashboard.html")
        assert ok.status_code == 200 and b"dash" in ok.content
        # A file that EXISTS one level up: only the containment guard says no.
        assert (work.parent / "FakeLib" / "refs" / "testing.json").is_file()
        outside = await user.http_client.get(
            "/dashboard/%2e%2e/FakeLib/refs/testing.json"
        )
        assert outside.status_code == 404
        missing = await user.http_client.get("/dashboard/nope.html")
        assert missing.status_code == 404
        shell.session.close()
        gone = await user.http_client.get("/dashboard/dashboard.html")
        assert gone.status_code == 409


async def test_close_project_refuses_while_edits_are_pending(shell):
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.stage([LOOSEN])
        await user.should_see("2 pending")
        user.find("Close project").click()
        await user.should_see("would be lost")
        assert (
            shell.session.project is not None and len(shell.session.spec.pending) == 2
        )


async def test_checkboxes_and_the_single_click_stay_in_step(shell):
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.select(tests={A, B})
        await user.should_see("2 tests selected")
        user.find(marker=D).click()
        await user.should_see(D)  # the centre's title
        assert shell.session.selection.tests == {D}
        await _settled(
            lambda: sum(c.value for c in user.find(ui.checkbox).elements) == 1
        )


async def _settled(predicate, tries: int = 40) -> None:
    """Refreshes are deferred; wait for a region's rebuild to land."""
    import asyncio

    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0.05)
    assert predicate()


async def test_the_filter_survives_a_click(shell):
    """Typed over 300 tests, the filter must not vanish on the next event."""
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        box = next(iter(user.find(ui.input).elements))
        box.set_value("FakeLib.A")
        await user.should_not_see(B)  # the filter's refresh landed
        rebuilt_from = id(next(iter(user.find(ui.input).elements)))
        user.find(marker=A).click()
        await user.should_see("/metrics/children/0")
        # The selection event rebuilds the list region; wait for that rebuild.
        await _settled(
            lambda: id(next(iter(user.find(ui.input).elements))) != rebuilt_from
        )
        with pytest.raises(AssertionError):
            user.find(marker=B)  # still filtered
        assert next(iter(user.find(ui.input).elements)).value == "FakeLib.A"


async def test_a_failing_advisory_leaf_reads_warn_not_fail(shell):
    """A warn-wrapped leaf that fails does not fail the test; the row says so."""
    from dstf.authoring.spec_document import PatchOp

    shell.session.stage(
        [
            PatchOp(
                A, "add", "/metrics/children/0/tolerance", 0.5
            ),  # the hard leaf passes
            PatchOp(
                A,
                "add",
                "/metrics/children/-",
                {
                    "combinator": "warn",
                    "children": [{"metric": "nrmse", "variable": "x"}],
                },
            ),
        ]
    )
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        user.find(marker=A).click()
        await user.should_see("/metrics/children/1/children/0")
        rows = [
            b
            for b in user.find(ui.button).elements
            if str(b.text).startswith("/metrics/children/1/children/0")
        ]
        assert rows and all("WARN" in r.text and "FAIL" not in r.text for r in rows)


async def test_a_lock_refusal_clears_when_the_job_ends_not_when_it_starts(shell):
    from dstf.authoring.capabilities import Job

    class _Cap:
        name, label, scope = "boom", "Boom", caps.Scope.PROJECT

        def available(self, s):
            return True

        def panel(self, s, ui_panel):
            pass

        def act(self, s, **inputs):
            raise RuntimeError("kept until the job ends")

    caps.register_capability(_Cap())
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.act(_Cap())
        assert "boom" in shell.errors
        gate = _Gate()
        shell.session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
        await user.should_see("running: rerun")
        assert "boom" in shell.errors  # a job STARTING clears nothing
        gate.release.set()
        await user.should_see("no job running")
        assert "boom" not in shell.errors


def test_the_figure_hook_names_the_primitive_from_meta_id():
    from dstf.authoring.app import FigureHook

    hook = FigureHook()
    seen = []
    hook.on("plotly_click", lambda primitive, payload: seen.append(primitive))
    hook._dispatch(
        "plotly_click",
        {
            "points": [
                {
                    "curveNumber": 0,
                    "data": {"meta": {"id": "/metrics/children/0/tube/band"}},
                }
            ]
        },
    )
    hook._dispatch(
        "plotly_click",
        {"points": [{"curveNumber": 0, "customdata": "/metrics/children/1/points/2"}]},
    )
    hook._dispatch("plotly_click", {"points": [{"curveNumber": 0}]})
    hook._dispatch("plotly_click", None)
    assert seen == [
        "/metrics/children/0/tube/band",
        "/metrics/children/1/points/2",
        "0",
        "",
    ]


async def test_a_results_only_event_refreshes_the_verdict(shell):
    """A rescore that stages nothing (D's tree is materializable, but a
    rescore with no change emits `results` only) must still redraw."""
    from dstf.authoring.capabilities import StoreWrite

    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        user.find(marker=A).click()
        await user.should_see("FAIL")
        work = shell.session.project.config.work_dir
        import json as _json

        t = [i / 4 for i in range(5)]
        (work / "test_0001" / "result.json").write_text(
            _json.dumps({"success": True, "time": t, "variables": {"x": t}}),
            encoding="utf-8",
        )
        shell.session.apply(
            StoreWrite(run=lambda store: None, models=frozenset({B}))
        )  # reloads results
        await user.should_see("PASS  NRMSE")


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
