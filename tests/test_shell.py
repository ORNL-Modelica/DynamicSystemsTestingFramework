"""The shell (§4.6 "The shell", §4.7): NiceGUI's in-process user simulation,
no browser. The shell is fixed and knows no capability by name."""

from __future__ import annotations

import json
import time

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


def _write_status(work, *, start_wall, rows) -> None:
    """A ``status.json`` in ProgressReporter's shape. The poll reads it
    through ``progress.read_status``, as the live dashboard does."""
    (work / "status.json").write_text(
        json.dumps(
            {
                "total": len(rows),
                "counts": {"running": sum(r.get("status") == "running" for r in rows)},
                "tests": rows,
                "start_wall": start_wall,
                "updated_at": time.time(),
                "metadata": {"backend_name": "Python"},
            }
        ),
        encoding="utf-8",
    )


async def test_the_job_panel_shows_per_test_rows_from_the_running_status(shell):
    """§4.7: progress is the ``status.json`` the pipeline already writes,
    polled — no log tail. The idle label stays; the outcome is a second line."""
    from dstf.authoring.capabilities import Job

    work = shell.session.project.config.work_dir
    gate = _Gate()

    def write_status_then_wait(ctx):
        _write_status(
            work,
            start_wall=time.time(),
            rows=[
                {
                    "test_key": "test_0001",
                    "model_id": A,
                    "status": "running",
                    "phase": "simulating",
                    "elapsed": 0.5,
                    "detail": None,
                }
            ],
        )
        gate(ctx)

    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.apply(
            Job(run=write_status_then_wait, label="rerun", models=frozenset({A}))
        )
        await user.should_see("running: rerun")
        # The poll is 1 s; should_see's default budget is 0.3 s.
        await user.should_see("simulating", retries=20)
        gate.release.set()
        await user.should_see("finished: rerun", retries=20)
        await user.should_see("no job running")
        await user.should_not_see("simulating")
        assert shell.session.running is None
        last = shell.session.last_job
        assert last is not None and last.error is None


async def test_a_status_from_an_older_run_is_not_this_jobs_progress(shell):
    """``RunningJob.started_wall`` against the snapshot's ``start_wall``: the
    previous run's rows are never shown as this job's."""
    from dstf.authoring.capabilities import Job

    work = shell.session.project.config.work_dir
    _write_status(
        work,
        start_wall=time.time() - 3600,
        rows=[
            {
                "test_key": "test_0001",
                "model_id": A,
                "status": "failed",
                "phase": None,
                "elapsed": 2.0,
            }
        ],
    )
    gate = _Gate()
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
        # The `started` event refreshes `jobs`, so `_progress_rows` has
        # already run against the stale file by the time this returns: the
        # absence is the guard's doing, not a timer that has yet to fire.
        await user.should_see("running: rerun")
        await user.should_not_see("failed")
        # Still the stale file: the gated job wrote no snapshot of its own.
        assert (
            json.loads((work / "status.json").read_text(encoding="utf-8"))["start_wall"]
            < shell.session.running.started_wall
        )
        gate.release.set()
        await user.should_see("no job running", retries=20)


async def test_a_job_that_raises_shows_its_failure_and_the_message(shell):
    """§4.7 "Failure handling": the job panel carries the message."""
    from dstf.authoring.capabilities import Job

    def boom(ctx):
        raise RuntimeError("no omc")

    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.apply(Job(run=boom, label="rerun", models=frozenset({A})))
        await user.should_see("failed: rerun", retries=20)
        await user.should_see("no omc", retries=20)
        assert shell.session.last_job is not None
        assert isinstance(shell.session.last_job.error, RuntimeError)


async def test_a_store_writes_line_is_shown_in_the_footer(shell):
    from dstf.authoring.capabilities import StoreWrite

    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.apply(
            StoreWrite(run=lambda store: "stored 1", models=frozenset({B}))
        )
        await user.should_see("stored 1")
        assert shell.session.notices == ["stored 1"]


async def test_a_finished_jobs_failed_result_shows_its_error_message(shell):
    """§4.7: "the backend's failure message from the results" — reloaded by
    the job's completion, one row per model the job ran."""
    from dstf.authoring.capabilities import Job

    work = shell.session.project.config.work_dir
    gate = _Gate()

    def fail_a_then_wait(ctx):
        # The Python driver's failing shape; read_result maps it to error_message.
        (work / "test_0001" / "result.json").write_text(
            json.dumps({"success": False, "error": "division by zero"}),
            encoding="utf-8",
        )
        gate(ctx)

    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.apply(
            Job(run=fail_a_then_wait, label="rerun", models=frozenset({A}))
        )
        await user.should_see("running: rerun")
        gate.release.set()
        await user.should_see("finished: rerun", retries=20)
        await user.should_see("division by zero", retries=20)
        assert shell.session.project.results[A].error_message == "division by zero"


async def test_a_failed_job_shows_no_result_rows_from_before_it(tmp_path):
    """A failed job reloads nothing, so the results under it are the ones
    that were already there — an old failure (or a bare model id, reading as
    "ran fine") would sit under `failed:` as if this job had produced it."""
    from dstf.authoring.capabilities import Job

    cfg, work = _project(tmp_path)
    (work / "test_0001" / "result.json").write_text(
        json.dumps({"success": False, "error": "OLD FAILURE"}), encoding="utf-8"
    )
    shell = Shell()
    shell.open(cfg, work_dir=work)
    assert shell.session.project.results[A].error_message == "OLD FAILURE"

    def boom(ctx):
        raise RuntimeError("no omc")

    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.apply(Job(run=boom, label="rerun", models=frozenset({A})))
        await user.should_see("failed: rerun", retries=20)
        await user.should_not_see("OLD FAILURE", retries=20)
        # The result itself is untouched: what changed is whose outcome it is.
        assert shell.session.project.results[A].error_message == "OLD FAILURE"


async def test_a_job_that_reloads_nothing_shows_no_result_rows(tmp_path):
    """The same defect as the failed job's, on the succeeding path: a job
    that asked for no reload leaves the previous run's results in place, so
    its `finished:` line must not stand over them either. The gate is the
    reload, not the outcome."""
    from dstf.authoring.capabilities import Job

    cfg, work = _project(tmp_path)
    (work / "test_0001" / "result.json").write_text(
        json.dumps({"success": False, "error": "OLD FAILURE"}), encoding="utf-8"
    )
    shell = Shell()
    shell.open(cfg, work_dir=work)
    assert shell.session.project.results[A].error_message == "OLD FAILURE"

    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.apply(
            Job(
                run=lambda ctx: None,
                label="export",
                models=frozenset({A}),
                reloads_results=False,
            )
        )
        await user.should_see("finished: export", retries=20)
        await user.should_not_see("OLD FAILURE", retries=20)
        with pytest.raises(AssertionError):  # every row a rebuild would draw
            user.find(marker="job-row")
        last = shell.session.last_job
        assert last is not None and last.error is None and last.reloaded is False


async def test_a_malformed_status_snapshot_renders_what_it_has(shell):
    """`_jobs` may not raise: ``status.json`` is a file, and its shapes are
    whatever wrote it. Each bad shape drops the rows and leaves the rest of
    the footer standing — the notice BELOW the rows is what proves a raise
    would have been caught here, since a partial rebuild would lose it.

    Every snapshot's ``start_wall`` is sampled at WRITE time (the job is
    already running by then, so it is never stale): a timestamp taken before
    the page opened would fall outside the staleness guard's 0.1 s slack and
    that guard, not the shape guard under test, would be what dropped the
    rows."""
    from dstf.authoring.capabilities import Job, StoreWrite

    work = shell.session.project.config.work_dir
    good_row = {
        "test_key": "test_0001",
        "model_id": A,
        "status": "running",
        "phase": "simulating",
        "elapsed": 0.5,
    }
    malformed = [
        lambda now: {"start_wall": "abc", "tests": [good_row]},
        lambda now: {"start_wall": now, "tests": 5},
        lambda now: {"start_wall": now, "tests": [{"model_id": ["x"]}]},
        # `json.dumps` writes the literal `NaN` and `json.loads` reads it back:
        # a float that passes the isinstance check and loses every comparison.
        lambda now: {"start_wall": float("nan"), "tests": [good_row]},
    ]
    gate = _Gate()
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.apply(
            StoreWrite(run=lambda store: "stored 1", models=frozenset({B}))
        )
        await user.should_see("stored 1")
        shell.session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
        await user.should_see("running: rerun")
        for make_status in malformed:
            # A good snapshot first, so the row's disappearance proves the
            # footer was rebuilt against the bad one and not left as it was.
            _write_status(work, start_wall=time.time(), rows=[good_row])
            shell._refresh("jobs")
            await user.should_see("simulating", retries=20)
            (work / "status.json").write_text(
                json.dumps(make_status(time.time())), encoding="utf-8"
            )
            shell._refresh("jobs")
            await user.should_not_see("simulating", retries=20)
            await user.should_see("running: rerun")
            await user.should_see("stored 1")  # nothing below the rows was lost
        gate.release.set()
        await user.should_see("no job running", retries=20)


async def test_the_last_jobs_outcome_is_the_projects_and_goes_with_it(shell):
    """The last job is the Session's, not the shell's: LibA's `finished:`
    line must not sit under "no project open" (nor under LibB after a swap)."""
    from dstf.authoring.capabilities import Job

    gate = _Gate()
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
        await user.should_see("running: rerun")
        gate.release.set()
        await user.should_see("finished: rerun", retries=20)
        user.find("Close project").click()
        await user.should_see("no project open")
        await user.should_not_see("finished: rerun", retries=20)
        assert shell.session.last_job is None


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

    def b_hidden() -> bool:
        try:
            user.find(marker=B)
        except AssertionError:
            return True
        return False

    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        box = next(iter(user.find(ui.input).elements))
        box.set_value("FakeLib.A")
        await _settled(b_hidden)  # the filter's refresh landed
        user.find(marker=A).click()
        await user.should_see("/metrics/children/0")  # the selection's refresh landed
        # The list region was rebuilt by the selection event too: the
        # filter text and its effect must both have survived that rebuild.
        await _settled(
            lambda: (
                b_hidden()
                and next(iter(user.find(ui.input).elements)).value == "FakeLib.A"
            )
        )
        assert shell.needle == "FakeLib.A"


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


async def test_the_figure_hook_does_not_accumulate_handlers_across_tab_rebuilds(shell):
    """§7.2: on-plot editing subscribes in panel(); the tabs region rebuilds
    on every selection, so the shell clears the hook before each build."""

    class _Sub:
        name, label, scope = "sub", "Sub", caps.Scope.LEAF

        def available(self, s):
            return True

        def panel(self, s, ui_panel):
            ui_panel.figure.on("plotly_click", lambda primitive, payload: None)

        def act(self, s, **inputs):
            return None

    caps.register_capability(_Sub())
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        for _ in range(3):
            shell.session.select(
                tests={A}, variable="x", leaf_path="/metrics/children/0"
            )
            await user.should_see("Sub")
            shell.session.select(tests={A}, variable="x", leaf_path=None)
            await _settled(
                lambda: "Sub" not in [t.label for t, _ in shell.applicable()]
            )
        shell.session.select(tests={A}, variable="x", leaf_path="/metrics/children/0")
        await user.should_see("Sub")
        await _settled(
            lambda: len(shell.figure_hook._handlers.get("plotly_click", [])) == 1
        )
        assert len(shell.figure_hook._handlers["plotly_click"]) == 1


async def test_a_plain_click_selects_the_first_variable(shell):
    """A VARIABLE-scope capability applies as soon as a plot is up."""
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        user.find(marker=A).click()
        await user.should_see("/metrics/children/0")
        assert shell.session.selection.variable == "x"


async def test_ticking_one_test_selects_its_first_variable(shell):
    """A single tick puts the same plot up as a plain click, so a
    VARIABLE-scope capability (Edit on plot) applies to it too."""

    class _OnPlot:
        name, label, scope = "on-plot", "OnPlot", caps.Scope.VARIABLE

        def available(self, s):
            return True

        def panel(self, s, ui_panel):
            pass

        def act(self, s, **inputs):
            return None

    caps.register_capability(_OnPlot())
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        (button,) = user.find(marker=A).elements
        # the checkbox in A's row (find()'s elements are not in page order)
        (box,) = [c for c in button.parent_slot.parent if isinstance(c, ui.checkbox)]
        box.set_value(True)
        await _settled(lambda: shell.session.selection.tests == {A})
        assert shell.session.selection.variable == "x"
        assert shell.session.selection.leaf_path is None
        assert "OnPlot" in [c.label for c, _ in shell.applicable()]


async def test_a_capabilitys_error_does_not_follow_to_another_selection(shell):
    shell.errors["edit-criteria"] = "ValueError: from leaf 0"
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        user.find(marker=D).click()
        await user.should_see(D)
        assert shell.errors == {}


async def test_project_less_header_keeps_quit(tmp_path):
    s = Shell()
    async with user_simulation(root=s.root) as user:
        await user.open("/")
        await user.should_see("Quit")


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


# -- the centre figure updates in place (owner ruling 2) --------------------


async def _select_a_plot(user):
    user.find(marker=A).click()
    await user.should_see("/metrics/children/0")
    (plot,) = user.find(ui.plotly).elements
    return plot


async def _stage_loosen_and_wait(shell, user, plot) -> None:
    """Stage LOOSEN on A; wait for the tree row's rescored text (a bare
    "PASS" would match D's and G's list badges before any rescore) and for
    the tick's redraw — in place, or (what the identity assertions after
    this rule out) by replacing the element."""
    rev = plot.figure["layout"].get("editrevision")
    shell.session.stage([LOOSEN])
    await user.should_see("PASS  NRMSE 1.531e-01", retries=20)
    await _settled(
        lambda: plot.is_deleted or plot.figure["layout"].get("editrevision") != rev
    )


async def test_a_staged_edit_keeps_the_plot_element(shell):
    """A rebuilt plot element loses the user's zoom; a staged edit keeps it
    and redraws the figure through `update_figure`."""
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        plot = await _select_a_plot(user)
        plot_id, rev0 = plot.id, plot.figure["layout"].get("editrevision")
        await _stage_loosen_and_wait(shell, user, plot)
        assert [p.id for p in user.find(ui.plotly).elements] == [plot_id]
        assert plot.figure["layout"]["editrevision"] != rev0
        assert shell.session.comparisons[A].passed
        assert shell.session.selection.tests == {A}


async def test_a_selection_change_rebuilds_the_plot_element(shell):
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        plot = await _select_a_plot(user)
        user.find(marker=D).click()
        await user.should_see(D)
        await _settled(lambda: plot.is_deleted)
        (new,) = user.find(ui.plotly).elements
        assert new.id != plot.id
        assert shell.session.selection.tests == {D}


async def test_a_results_only_event_redraws_the_plot_in_place(shell):
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        plot = await _select_a_plot(user)
        # Every fixture test is flat: a first `rescore` would STAGE the
        # materialization and emit `spec`. After LOOSEN it is a pure rescore.
        await _stage_loosen_and_wait(shell, user, plot)
        rev = plot.figure["layout"]["editrevision"]
        shell.session.rescore(A)
        await _settled(lambda: plot.figure["layout"]["editrevision"] != rev)
        assert [p.id for p in user.find(ui.plotly).elements] == [plot.id]


async def test_one_stage_redraws_the_figure_exactly_once(shell):
    """`stage` emits `spec` then `results` synchronously, with no tick
    between them: the dirty flag makes that one redraw, not two."""
    import asyncio

    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        plot = await _select_a_plot(user)
        calls = [0]
        original = plot.update_figure

        def counting(figure):
            calls[0] += 1
            original(figure)

        plot.update_figure = counting
        shell.session.stage([LOOSEN])
        await _settled(lambda: calls[0] >= 1)
        await asyncio.sleep(0.25)  # two more ticks
        assert calls[0] == 1
        assert shell.session.comparisons[A].passed


async def test_a_shape_drag_redraws_the_figure_and_stages_nothing(shell):
    """The hook's shape-moved callback re-sends the figure whether or not
    anything was staged, so a dragged shape snaps back."""
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        plot = await _select_a_plot(user)
        pending_before = list(shell.session.spec.pending)
        rev = plot.figure["layout"].get("editrevision")
        shell.figure_hook._dispatch("plotly_relayout", {"shapes[0].y0": 1.0})
        await _settled(lambda: plot.figure["layout"].get("editrevision") != rev)
        assert [p.id for p in user.find(ui.plotly).elements] == [plot.id]
        assert shell.session.spec.pending == pending_before


async def test_a_changed_name_list_rebuilds_the_plot_and_the_picker(shell):
    """The picker's names are part of the centre's key: a staged tree that
    adds a variable must rebuild the centre, or the select keeps the old
    options."""
    from dstf.authoring.spec_document import PatchOp

    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        plot = await _select_a_plot(user)
        shell.session.stage(
            [
                PatchOp(
                    A,
                    "add",
                    "/metrics",
                    {
                        "combinator": "and",
                        "children": [
                            {"metric": "nrmse", "variable": "x"},
                            {"metric": "range", "variable": "y", "max_value": 5.0},
                        ],
                    },
                )
            ]
        )
        await _settled(lambda: plot.is_deleted)
        (select,) = user.find(ui.select).elements
        assert "y" in select.options
        (new,) = user.find(ui.plotly).elements
        assert new.id != plot.id


async def test_a_dirty_figure_is_not_redrawn_for_a_selection_it_was_not_built_for(
    shell,
):
    """Between a selection change and the deferred centre rebuild the old
    plot element still exists: the tick must not draw the new selection's
    figure into it."""
    import asyncio

    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        plot = await _select_a_plot(user)
        calls = [0]
        original = plot.update_figure

        def counting(figure):
            calls[0] += 1
            original(figure)

        plot.update_figure = counting
        shell._mark_figure_dirty()
        shell.session.select(tests={D}, variable="x", leaf_path=None)
        assert not plot.is_deleted  # the rebuild has not run yet
        shell._tick()
        await _settled(lambda: plot.is_deleted)
        await asyncio.sleep(0.25)
        assert calls[0] == 0


async def test_a_tree_refresh_after_close_does_not_raise(shell):
    """A results event defers a `_tree` refresh; a close in the same pass
    runs before it, so the refresh finds no project."""
    import asyncio

    from nicegui import app

    errors: list[Exception] = []
    async with user_simulation(root=shell.root) as user:
        app.on_exception(errors.append)
        try:
            await user.open("/")
            await _select_a_plot(user)
            shell.session._reload_results()
            shell.session.close()
            await user.should_see("dstf author --config")
            await asyncio.sleep(0.25)  # every deferred refresh has run
        finally:
            app._exception_handlers.remove(errors.append)
    assert errors == []
    assert shell.session.project is None


async def test_a_tree_row_click_keeps_the_plot_element(shell):
    """A leaf click changes only `leaf_path`: the centre's key is the same,
    so the plot (and the user's zoom) stays."""
    import asyncio

    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        plot = await _select_a_plot(user)
        user.find("/metrics/children/0").click()
        await _settled(
            lambda: shell.session.selection.leaf_path == "/metrics/children/0"
        )
        await user.should_see("/metrics/children/0")
        await asyncio.sleep(0.25)  # a deferred centre rebuild would have run
        assert not plot.is_deleted
        assert [p.id for p in user.find(ui.plotly).elements] == [plot.id]


# -- panels follow edits made elsewhere (Conventions call 6) ----------------


def _range_leaf(max_value: float) -> dict:
    from test_handles import TREE

    return {**TREE["children"][1], "max_value": max_value}


def _stage_tree_on_d(shell) -> None:
    from dstf.authoring.spec_document import PatchOp
    from test_handles import TREE

    shell.session.stage([PatchOp(D, "add", "/metrics", TREE)])


class _Setter:
    """VARIABLE scope; its panel is one input and one relayout handler; its
    act replaces D's child 1 with the ``leaf`` it is given (or raises
    ``boom``). ``on_relayout`` is what the handler does; ``builds`` counts
    panel builds."""

    name, label, scope = "setter", "Setter", caps.Scope.VARIABLE

    def __init__(self):
        self.ui_panel = None
        self.builds = 0
        self.relayouts: list[str] = []
        self.on_relayout = None

    def available(self, s):
        return True

    def panel(self, s, ui_panel):
        self.ui_panel = ui_panel
        self.builds += 1
        ui.input("setter").mark("setter-input")
        ui_panel.figure.on("plotly_relayout", self._relayout)

    def _relayout(self, primitive, payload):
        self.relayouts.append(primitive)
        if self.on_relayout is not None:
            self.on_relayout()

    def act(self, s, **inputs):
        from dstf.authoring.capabilities import Patch
        from dstf.authoring.spec_document import PatchOp

        if "boom" in inputs:
            raise RuntimeError(inputs["boom"])
        return Patch(ops=[PatchOp(D, "replace", "/metrics/children/1", inputs["leaf"])])


class _Listener:
    """A second relayout subscriber that never acts."""

    name, label, scope = "listener", "Listener", caps.Scope.VARIABLE

    def __init__(self):
        self.builds = 0
        self.relayouts: list[str] = []

    def available(self, s):
        return True

    def panel(self, s, ui_panel):
        self.builds += 1
        ui_panel.figure.on(
            "plotly_relayout",
            lambda primitive, payload: self.relayouts.append(primitive),
        )

    def act(self, s, **inputs):
        return None


async def _open_on_range_leaf(shell, user) -> None:
    """The page on D's range leaf (child 1), Edit criteria's form drawn."""
    await user.open("/")
    shell.session.select(tests={D}, variable="x", leaf_path="/metrics/children/1")
    await user.should_see("Edit criteria")
    await user.should_see("Upper bound")


def _upper_bound(user):
    """Edit criteria's range `max_value` control's value, None while absent."""
    try:
        (number,) = user.find(kind=ui.number, content="Upper bound (optional)").elements
    except (AssertionError, ValueError):
        return None
    return number.value


def _setter_input_id(user):
    (element,) = user.find(marker="setter-input").elements
    return element.id


async def test_a_panel_follows_an_edit_another_capability_staged(shell):
    setter = _Setter()
    caps.register_capability(setter)
    _stage_tree_on_d(shell)
    async with user_simulation(root=shell.root) as user:
        await _open_on_range_leaf(shell, user)
        assert _upper_bound(user) == 2.0
        setter.ui_panel.submit(leaf=_range_leaf(3.0))
        await _settled(lambda: shell.session.tree(D)["children"][1]["max_value"] == 3.0)
        await _settled(lambda: _upper_bound(user) == 3.0)
        assert "setter" not in shell.errors


async def test_the_actors_own_slot_is_not_rebuilt_by_its_panel_originated_act(shell):
    setter = _Setter()
    caps.register_capability(setter)
    _stage_tree_on_d(shell)
    async with user_simulation(root=shell.root) as user:
        await _open_on_range_leaf(shell, user)
        before = _setter_input_id(user)
        setter.ui_panel.submit(leaf=_range_leaf(3.0))
        await _settled(lambda: _upper_bound(user) == 3.0)  # the others rebuilt
        assert shell.session.tree(D)["children"][1]["max_value"] == 3.0
        assert _setter_input_id(user) == before


async def test_a_figure_originated_act_rebuilds_the_actors_slot_too(shell):
    setter = _Setter()
    caps.register_capability(setter)
    _stage_tree_on_d(shell)
    async with user_simulation(root=shell.root) as user:
        await _open_on_range_leaf(shell, user)
        before = _setter_input_id(user)
        builds = setter.builds
        setter.on_relayout = lambda: setter.ui_panel.submit(leaf=_range_leaf(3.0))
        # D's plot is attached; its first shape is the range leaf's min line.
        shell.figure_hook._dispatch("plotly_relayout", {"shapes[0].y0": 1.0})
        assert setter.relayouts == ["/metrics/children/1/range/min"]
        assert shell.session.tree(D)["children"][1]["max_value"] == 3.0
        await _settled(lambda: setter.builds > builds)
        await _settled(lambda: _setter_input_id(user) != before)
        assert _upper_bound(user) == 3.0


async def test_slot_rebuilds_do_not_accumulate_hook_handlers(shell):
    setter = _Setter()
    caps.register_capability(setter)
    _stage_tree_on_d(shell)
    async with user_simulation(root=shell.root) as user:
        await _open_on_range_leaf(shell, user)
        for value in (2.5, 3.0, 3.5):
            builds = setter.builds
            setter.on_relayout = lambda v=value: setter.ui_panel.submit(
                leaf=_range_leaf(v)
            )
            shell.figure_hook._dispatch("plotly_relayout", {"shapes[0].y0": 1.0})
            await _settled(lambda b=builds: setter.builds > b)
        assert shell.session.tree(D)["children"][1]["max_value"] == 3.5
        owners = [
            owner
            for entries in shell.figure_hook._handlers.values()
            for owner, _ in entries
        ]
        # What one build of each panel registers: `_Setter` one handler, and
        # so does every other panel present (Edit on plot, with TREE staged).
        assert "setter" in owners
        assert all(owners.count(owner) == 1 for owner in owners)


async def test_the_active_tab_is_kept_and_a_changed_availability_rebuilds_the_tabs(
    shell,
):
    class _Late:
        name, label, scope = "late", "Late", caps.Scope.VARIABLE

        def available(self, s):
            children = (s.tree(D) or {}).get("children") or []
            if len(children) > 1 and children[1].get("max_value") == 3.0:
                return True
            return "needs D's child 1 at max 3.0"

        def panel(self, s, ui_panel):
            ui.label("late panel")

        def act(self, s, **inputs):
            return None

    setter = _Setter()
    caps.register_capability(setter)
    caps.register_capability(_Late())
    _stage_tree_on_d(shell)
    async with user_simulation(root=shell.root) as user:
        await _open_on_range_leaf(shell, user)
        assert "late" not in shell._slots
        (panels,) = user.find(ui.tab_panels).elements
        panels.set_value("Setter")
        setter.ui_panel.submit(leaf=_range_leaf(2.5))
        await _settled(lambda: _upper_bound(user) == 2.5)
        assert panels.value == "Setter" and not panels.is_deleted
        setter.ui_panel.submit(leaf=_range_leaf(3.0))
        await user.should_see("late panel")
        assert "late" in shell._slots
        (tab,) = [
            t for t in user.find(ui.tab).elements if t._props.get("label") == "Late"
        ]
        assert tab.enabled
        (rebuilt,) = user.find(ui.tab_panels).elements
        assert rebuilt is not panels  # the tabs were rebuilt ...
        assert rebuilt.value == "Setter"  # ... and the active tab kept
        assert shell.session.tree(D)["children"][1]["max_value"] == 3.0


async def test_an_act_that_raises_shows_in_the_actors_slot(shell):
    setter = _Setter()
    caps.register_capability(setter)
    _stage_tree_on_d(shell)
    async with user_simulation(root=shell.root) as user:
        await _open_on_range_leaf(shell, user)
        (panels,) = user.find(ui.tab_panels).elements
        panels.set_value("Setter")
        pending = list(shell.session.spec.pending)
        tree = shell.session.tree(D)
        setter.ui_panel.submit(boom="act boom")
        await user.should_see("RuntimeError: act boom")
        (label,) = user.find(kind=ui.label, content="RuntimeError: act boom").elements
        assert _inside(label, shell._slots["setter"].container)
        assert shell.errors == {"setter": "RuntimeError: act boom"}
        assert panels.value == "Setter" and not panels.is_deleted
        assert shell.session.spec.pending == pending
        assert shell.session.tree(D) == tree


async def test_a_panel_whose_build_raises_shows_the_error_in_its_slot(shell):
    class _Broken:
        name, label, scope = "broken", "Broken", caps.Scope.VARIABLE

        def available(self, s):
            return True

        def panel(self, s, ui_panel):
            raise RuntimeError("panel boom")

        def act(self, s, **inputs):
            return None

    caps.register_capability(_Broken())
    caps.register_capability(_Setter())
    _stage_tree_on_d(shell)
    async with user_simulation(root=shell.root) as user:
        await _open_on_range_leaf(shell, user)
        await user.should_see("RuntimeError: panel boom")
        (label,) = user.find(kind=ui.label, content="RuntimeError: panel boom").elements
        assert _inside(label, shell._slots["broken"].container)
        assert _upper_bound(user) == 2.0  # the other tabs still render
        assert user.find(marker="setter-input").elements


async def test_panels_follow_a_discard(tmp_path):
    from test_handles import TREE

    cfg, work = _project(tmp_path)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    (entry,) = [e for e in spec["tests"] if e["model"] == D]
    entry["metrics"] = TREE
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    shell = Shell()
    shell.open(cfg, work_dir=work)
    assert shell.session.spec.pending == []  # loaded, not pending
    setter = _Setter()
    caps.register_capability(setter)
    async with user_simulation(root=shell.root) as user:
        await _open_on_range_leaf(shell, user)
        assert _upper_bound(user) == 2.0
        setter.ui_panel.submit(leaf=_range_leaf(3.0))
        await _settled(lambda: _upper_bound(user) == 3.0)
        user.find("Discard").click()
        await _settled(lambda: shell.session.spec.pending == [])
        await _settled(lambda: _upper_bound(user) == 2.0)
        assert shell.session.tree(D)["children"][1]["max_value"] == 2.0


async def test_a_success_clears_the_actors_old_error(shell):
    setter = _Setter()
    caps.register_capability(setter)
    _stage_tree_on_d(shell)
    async with user_simulation(root=shell.root) as user:
        await _open_on_range_leaf(shell, user)
        setter.ui_panel.submit(boom="act boom")
        await user.should_see("RuntimeError: act boom")
        setter.ui_panel.submit(leaf=_range_leaf(3.0))
        await _settled(lambda: _upper_bound(user) == 3.0)
        assert "setter" not in shell.errors
        slot = shell._slots["setter"].container
        assert not [
            e
            for e in slot.descendants()
            if isinstance(e, ui.label) and "act boom" in str(e.text)
        ]


async def test_a_success_clears_the_old_error_in_place_keeping_the_typed_in_field(
    shell,
):
    """A refusal refills the actor's slot; the success after it only empties
    the error label — a refill would destroy the field being typed in."""
    import asyncio

    class _Typist:
        name, label, scope = "typist", "Typist", caps.Scope.VARIABLE

        def available(self, s):
            return True

        def panel(self, s, ui_panel):
            self.ui_panel = ui_panel
            ui.input("typing").mark("typing-input")

        def act(self, s, **inputs):
            from dstf.authoring.capabilities import Patch
            from dstf.authoring.spec_document import PatchOp

            if "boom" in inputs:
                raise RuntimeError(inputs["boom"])
            return Patch(
                ops=[PatchOp(D, "replace", "/metrics/children/1", inputs["leaf"])]
            )

    typist = _Typist()
    caps.register_capability(typist)
    _stage_tree_on_d(shell)
    async with user_simulation(root=shell.root) as user:
        await _open_on_range_leaf(shell, user)
        (panels,) = user.find(ui.tab_panels).elements
        panels.set_value("Typist")

        def error_texts() -> list[str]:
            slot = shell._slots["typist"].container
            return [
                str(e.text)
                for e in slot.descendants()
                if isinstance(e, ui.label) and "type boom" in str(e.text)
            ]

        typist.ui_panel.submit(boom="type boom")
        await user.should_see("RuntimeError: type boom")
        assert error_texts() == ["RuntimeError: type boom"]
        (field,) = user.find(marker="typing-input").elements
        typist.ui_panel.submit(leaf=_range_leaf(3.0))
        assert shell.session.tree(D)["children"][1]["max_value"] == 3.0
        assert "typist" not in shell.errors
        assert error_texts() == []
        await asyncio.sleep(0.25)  # two ticks: any deferred rebuild has run
        assert error_texts() == []
        assert [e.id for e in user.find(marker="typing-input").elements] == [field.id]
        assert not field.is_deleted


async def test_panels_read_the_rescored_comparisons(shell):
    from dstf.figures.assemble import leaves_by_variable

    class _Paths:
        name, label, scope = "paths", "Paths", caps.Scope.VARIABLE

        def available(self, s):
            return True

        def panel(self, s, ui_panel):
            for path, _ in leaves_by_variable(s.comparisons[D].metric_tree)["x"]:
                ui.label(path)

        def act(self, s, **inputs):
            return None

    caps.register_capability(_Paths())
    _stage_tree_on_d(shell)
    edit_tree = next(c for c in caps.registered_capabilities() if c.name == "edit-tree")
    async with user_simulation(root=shell.root) as user:
        await _open_on_range_leaf(shell, user)

        def paths_shown() -> list[str]:
            slot = shell._slots["paths"].container
            return [str(e.text) for e in slot.descendants() if isinstance(e, ui.label)]

        assert "/metrics/children/1" in paths_shown()
        shell.act(
            edit_tree,
            action="wrap",
            model_id=D,
            path="/metrics/children/1",
            kind="warn",
        )
        assert shell.errors == {}
        assert shell.session.tree(D)["children"][1]["combinator"] == "warn"
        await _settled(lambda: "/metrics/children/1/children/0" in paths_shown())
        assert "/metrics/children/1" not in paths_shown()


async def test_a_rebuild_is_deferred_out_of_the_figure_hooks_dispatch(shell):
    """Two subscribers to one event, the first acting on it: a rebuild inside
    the dispatch would clear the second's handler before it heard the event."""
    setter, listener = _Setter(), _Listener()
    caps.register_capability(setter)
    caps.register_capability(listener)
    _stage_tree_on_d(shell)
    async with user_simulation(root=shell.root) as user:
        await _open_on_range_leaf(shell, user)
        builds = (setter.builds, listener.builds)
        setter.on_relayout = lambda: setter.ui_panel.submit(leaf=_range_leaf(3.0))
        shell.figure_hook._dispatch("plotly_relayout", {"shapes[0].y0": 1.0})
        assert (setter.builds, listener.builds) == builds  # not inside it
        assert setter.relayouts == ["/metrics/children/1/range/min"]
        assert listener.relayouts == ["/metrics/children/1/range/min"]
        assert shell.session.tree(D)["children"][1]["max_value"] == 3.0
        await _settled(
            lambda: setter.builds > builds[0] and listener.builds > builds[1]
        )
        assert _upper_bound(user) == 3.0


def _active_tab(user) -> str:
    (panels,) = user.find(ui.tab_panels).elements
    return panels.value


def _enabled_labels(shell) -> list[str]:
    return [cap.label for cap, avail in shell.applicable() if avail is True]


async def test_a_selection_change_lands_on_the_first_enabled_tab(shell):
    """A leaf click lands on Edit criteria (registered first) even when the
    plain selection before it had another tab active."""
    _stage_tree_on_d(shell)
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.select(tests={D}, variable="x", leaf_path=None)
        await user.should_see("Edit tree")
        (panels,) = user.find(ui.tab_panels).elements
        panels.set_value("Edit tree")
        shell.session.select(tests={D}, variable="x", leaf_path="/metrics/children/1")
        await _settled(lambda: "edit-criteria" in shell._slots)
        await _settled(lambda: _active_tab(user) == "Edit criteria")
        assert _active_tab(user) == _enabled_labels(shell)[0]


async def test_a_selection_that_drops_the_active_tab_lands_on_an_enabled_one(shell):
    _stage_tree_on_d(shell)
    async with user_simulation(root=shell.root) as user:
        await _open_on_range_leaf(shell, user)
        assert _active_tab(user) == "Edit criteria"
        shell.session.select(tests={A}, variable="x", leaf_path=None)
        await _settled(lambda: "edit-criteria" not in shell._slots and shell._slots)
        await _settled(lambda: _active_tab(user) in _enabled_labels(shell))
        active = _active_tab(user)
        assert active != "Edit criteria"
        (cap,) = [c for c, _ in shell.applicable() if c.label == active]
        container = shell._slots[cap.name].container
        assert not container.is_deleted


async def test_an_availability_change_that_drops_the_active_tab_lands_on_an_enabled_one(
    shell,
):
    """The `previous in enabled` guard, on a rebuild no selection caused."""

    class _Fading:
        name, label, scope = "fading", "Fading", caps.Scope.VARIABLE

        def available(self, s):
            children = (s.tree(D) or {}).get("children") or []
            if len(children) > 1 and children[1].get("max_value") == 3.0:
                return "gone once D's child 1 is at max 3.0"
            return True

        def panel(self, s, ui_panel):
            ui.label("fading panel")

        def act(self, s, **inputs):
            return None

    setter = _Setter()
    caps.register_capability(setter)
    caps.register_capability(_Fading())
    _stage_tree_on_d(shell)
    async with user_simulation(root=shell.root) as user:
        await _open_on_range_leaf(shell, user)
        (panels,) = user.find(ui.tab_panels).elements
        panels.set_value("Fading")
        setter.ui_panel.submit(leaf=_range_leaf(3.0))
        await _settled(lambda: "fading" not in shell._slots and shell._slots)
        await _settled(lambda: _active_tab(user) in _enabled_labels(shell))
        assert _active_tab(user) != "Fading"


class _Rescorer:
    """VARIABLE scope; its panel rescores D once per build, then renders one
    label."""

    name, label, scope = "rescorer", "Rescorer", caps.Scope.VARIABLE

    def __init__(self):
        self.builds = 0

    def available(self, s):
        return True

    def panel(self, s, ui_panel):
        self.builds += 1
        s.rescore(D)
        ui.label("rescored").mark("rescorer-label")

    def act(self, s, **inputs):
        return None


async def test_a_results_event_during_a_panel_build_is_deferred_not_re_entered(shell):
    import asyncio

    rescorer = _Rescorer()
    caps.register_capability(rescorer)
    _stage_tree_on_d(shell)
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.select(tests={D}, variable="x", leaf_path=None)
        await _settled(lambda: "rescorer" in shell._slots)
        await _settled(lambda: shell._panels_pending is None)

        def labels() -> list:
            slot = shell._slots["rescorer"].container
            return [
                e
                for e in user.find(marker="rescorer-label").elements
                if _inside(e, slot)
            ]

        await _settled(lambda: len(labels()) == 1)
        await asyncio.sleep(0.25)  # two ticks
        builds = rescorer.builds
        await asyncio.sleep(0.25)  # two more
        assert rescorer.builds == builds  # no growth
        assert len(labels()) == 1
        assert len(user.find(marker="rescorer-label").elements) == 1


async def test_a_close_rebuilds_the_tabs(shell):
    setter = _Setter()
    caps.register_capability(setter)
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.select(tests={D}, variable="x", leaf_path=None)
        await _settled(lambda: "setter" in shell._slots)
        assert shell.figure_hook._handlers  # the setter's handler, at least
        shell.session.close()
        await user.should_see("nothing applies to this selection", retries=20)
        await _settled(lambda: shell._slots == {})
        assert not any(shell.figure_hook._handlers.values())


async def test_an_act_error_during_a_dispatch_is_shown_after_the_tick(shell):
    setter = _Setter()
    caps.register_capability(setter)
    _stage_tree_on_d(shell)
    async with user_simulation(root=shell.root) as user:
        await _open_on_range_leaf(shell, user)
        setter.on_relayout = lambda: setter.ui_panel.submit(boom="drag boom")

        def error_labels() -> list:
            slot = shell._slots["setter"].container
            return [
                e
                for e in slot.descendants()
                if isinstance(e, ui.label) and "drag boom" in str(e.text)
            ]

        shell.figure_hook._dispatch("plotly_relayout", {"shapes[0].y0": 1.0})
        assert shell.errors["setter"] == "RuntimeError: drag boom"
        assert not error_labels()  # not rebuilt inside the dispatch
        assert "setter" in (shell._panels_pending or set())
        await user.should_see("RuntimeError: drag boom", retries=20)
        assert error_labels()
