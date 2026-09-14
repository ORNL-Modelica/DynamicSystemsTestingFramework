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


class _StandInPlot:
    """What `FigureHook.attach` needs of a `ui.plotly`: a `figure` dict and
    an `on` that records registrations. No page."""

    def __init__(self, figure=None):
        self.figure = figure if figure is not None else {}
        self.registered = []

    def on(self, event, handler):
        self.registered.append(event)


def _recording_hook(plot=None):
    from dstf.authoring.app import FigureHook

    moved = []
    hook = FigureHook(on_shape_moved=lambda: moved.append(True))
    seen = []
    for kind in ("plotly_click", "plotly_relayout", "plotly_selected"):
        hook.on(
            kind,
            lambda primitive, payload, k=kind: seen.append((k, primitive, payload)),
        )
    if plot is not None:
        hook.attach(plot)
    return hook, seen, moved


def test_an_x_unified_click_names_the_handle_not_the_first_trace():
    """The spike: under the figure's `hovermode: "x unified"` a click on a
    handle lists the Actual trace first."""
    hook, seen, moved = _recording_hook()
    hook._dispatch(
        "plotly_click",
        {
            "event": {},
            "points": [
                {
                    "curveNumber": 0,
                    "pointNumber": 50,
                    "x": 5,
                    "y": 5,
                    "data": {"meta": {"id": "actual", "kind": "act"}},
                },
                {
                    "curveNumber": 1,
                    "pointNumber": 1,
                    "x": 5,
                    "y": 7,
                    "customdata": "/metrics/children/0/tube/upper/1",
                    "data": {"meta": {"family": "tube/upper", "kind": "handle"}},
                },
            ],
        },
    )
    assert [primitive for _, primitive, _ in seen] == [
        "/metrics/children/0/tube/upper/1"
    ]
    assert moved == []


_SHAPES = {
    "layout": {
        "shapes": [
            {"name": "/metrics/children/1/range/max"},
            {"name": "/metrics/children/2/points/box/0"},
            {"name": "/metrics/children/0/tube/lower/0"},
        ]
    }
}


def test_a_shape_relayout_is_dispatched_per_shape_with_the_rendered_name():
    """The spike's drag payloads carry the shape's index, never its name."""
    plot = _StandInPlot(_SHAPES)
    hook, seen, moved = _recording_hook(plot)
    assert plot.registered == ["plotly_click", "plotly_relayout", "plotly_selected"]
    cases = [
        (
            {
                "shapes[0].x0": 0,
                "shapes[0].x1": 10,
                "shapes[0].y0": 6.0,
                "shapes[0].y1": 6.0,
            },
            [
                (
                    "/metrics/children/1/range/max",
                    {"x0": 0, "x1": 10, "y0": 6.0, "y1": 6.0},
                )
            ],
        ),
        (
            {"shapes[2].xanchor": 4.0, "shapes[2].yanchor": 2.99},
            [("/metrics/children/0/tube/lower/0", {"xanchor": 4.0, "yanchor": 2.99})],
        ),
        (
            {"shapes[0].x0": 0, "shapes[0].y0": 2.0},
            [("/metrics/children/1/range/max", {"x0": 0, "y0": 2.0})],
        ),
        (
            {"shapes[2].xanchor": 4.0, "shapes[0].y0": 2.0, "shapes[2].yanchor": 2.99},
            [
                ("/metrics/children/1/range/max", {"y0": 2.0}),
                ("/metrics/children/0/tube/lower/0", {"xanchor": 4.0, "yanchor": 2.99}),
            ],
        ),
    ]
    for payload, expected in cases:
        seen.clear()
        moved.clear()
        hook._dispatch("plotly_relayout", payload)
        assert [(p, d) for _, p, d in seen] == expected
        assert moved == [True]  # once per relayout, however many shapes


def test_a_zoom_relayout_is_dispatched_once_raw_and_moves_no_shape():
    hook, seen, moved = _recording_hook(_StandInPlot(_SHAPES))
    zoom = {"xaxis.range[0]": 0.49, "xaxis.range[1]": 1.49}
    hook._dispatch("plotly_relayout", zoom)
    assert [(p, d) for _, p, d in seen] == [("", zoom)]
    assert moved == []


def test_nothing_malformed_makes_the_hook_dispatch_raise():
    """The docstring's "no malformed payload makes dispatch raise": every
    input it rules out, tried."""
    from dstf.authoring.app import FigureHook

    # A shape key whose index cannot be resolved: id "" for that shape, and
    # the shape-moved callback still runs.
    unresolvable = [
        (_StandInPlot(_SHAPES), {"shapes[9].y0": 1.0}, {"y0": 1.0}),
        (None, {"shapes[0].y0": 1.0}, {"y0": 1.0}),
        (
            _StandInPlot({"layout": {"shapes": [{"type": "line"}]}}),
            {"shapes[0].y0": 1.0},
            {"y0": 1.0},
        ),
        (_StandInPlot({"layout": {}}), {"shapes[0].y0": 1.0}, {"y0": 1.0}),
        (_StandInPlot("not a dict"), {"shapes[0].y0": 1.0}, {"y0": 1.0}),
    ]
    for plot, payload, stripped in unresolvable:
        hook, seen, moved = _recording_hook(plot)
        hook._dispatch("plotly_relayout", payload)
        assert [(p, d) for _, p, d in seen] == [("", stripped)]
        assert moved == [True]
    # A key NOT matching the shape pattern is any other relayout.
    for payload in ({"shapes[x].y0": 1.0}, {"shapes[0]": 1.0}):
        hook, seen, moved = _recording_hook(_StandInPlot(_SHAPES))
        hook._dispatch("plotly_relayout", payload)
        assert [(p, d) for _, p, d in seen] == [("", payload)]
        assert moved == []
    for kind in ("plotly_click", "plotly_relayout", "plotly_selected"):
        for payload in (
            None,
            [],
            {"points": [5, "x", None]},
            {"points": "not a list"},
            {"points": [{"curveNumber": 2, "data": {"meta": "not a dict"}}]},
            {"points": [{"data": "not a dict"}]},
        ):
            hook = FigureHook()
            got: list[str] = []
            hook.on(kind, lambda primitive, data, got=got: got.append(primitive))
            hook._dispatch(kind, payload)
            assert len(got) == 1 and isinstance(got[0], str)


def test_the_figure_hook_clears_handlers_by_owner():
    from dstf.authoring.app import FigureHook

    hook = FigureHook()
    got = []

    def h1(primitive, payload):
        got.append("h1")

    def h2(primitive, payload):
        got.append("h2")

    hook.owned("a").on("plotly_click", h1)
    hook.owned("b").on("plotly_click", h2)
    hook._dispatch("plotly_click", {})
    assert got == ["h1", "h2"]
    hook.clear("a")
    got.clear()
    hook._dispatch("plotly_click", {})
    assert got == ["h2"]
    hook.clear()
    got.clear()
    hook._dispatch("plotly_click", {})
    assert got == []


def test_the_figure_hook_dispatches_from_a_snapshot():
    """A handler registered during dispatch — a panel rebuilt by the act it
    triggered — is first called on the next event."""
    from dstf.authoring.app import FigureHook

    hook = FigureHook()
    calls = {"old": 0, "new": 0, "extra": 0}

    def bounded(name):
        calls[name] += 1
        if sum(calls.values()) > 5:
            raise AssertionError(f"dispatch did not stop: {calls}")

    def new(primitive, payload):
        bounded("new")

    def old(primitive, payload):
        bounded("old")
        hook.clear("a")  # what a slot rebuild does
        hook.owned("a").on("plotly_click", new)

    hook.owned("a").on("plotly_click", old)
    hook._dispatch("plotly_click", {})
    assert (calls["old"], calls["new"]) == (1, 0)
    hook._dispatch("plotly_click", {})
    assert (calls["old"], calls["new"]) == (1, 1)

    # A handler that registers another without clearing: the added one does
    # not run in the dispatch that added it.
    hook = FigureHook()
    calls.update(old=0, new=0, extra=0)

    def extra(primitive, payload):
        bounded("extra")

    def adder(primitive, payload):
        bounded("old")
        hook.owned("b").on("plotly_click", extra)

    hook.owned("a").on("plotly_click", adder)
    hook._dispatch("plotly_click", {})
    assert (calls["old"], calls["extra"]) == (1, 0)


def test_the_figure_hook_is_dispatching_only_while_handlers_run():
    from dstf.authoring.app import FigureHook

    hook = FigureHook()
    during = []
    hook.on("plotly_click", lambda primitive, payload: during.append(hook.dispatching))
    assert hook.dispatching is False
    hook._dispatch("plotly_click", {})
    assert during == [True]
    assert hook.dispatching is False

    def boom(primitive, payload):
        raise RuntimeError("boom")

    hook.on("plotly_click", boom)
    with pytest.raises(RuntimeError):
        hook._dispatch("plotly_click", {})
    assert hook.dispatching is False


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
