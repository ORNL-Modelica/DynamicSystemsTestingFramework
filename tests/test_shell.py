"""The new shell (roadmap §2; D129) in NiceGUI's in-process user simulation,
no browser: the process (`Shell`), the header, Save / Discard / Export, Stop
server, the pending drawer, the dashboard route, the progressive open and
the runs card. The Suite list is tests/test_suite_page.py; the buttons at a
placement tests/test_capability_conformance.py."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time

import pytest

pytest.importorskip("nicegui")

from nicegui import app  # noqa: E402
from nicegui.testing.user_simulation import user_simulation  # noqa: E402

from dstf.authoring import capabilities as caps  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.capabilities import Job  # noqa: E402
from dstf.authoring.shell.shell import Shell  # noqa: E402
from dstf.reporting.status import FAIL, classify  # noqa: E402
from test_session import LOOSEN, A, B, C, D, G, _Gate, _project  # noqa: E402


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
def shell(tmp_path):
    """A shell whose project is open synchronously — what a page sees once
    loading is over. The open's events sit in the shell's queue until the
    first tick replays them (scan → spec → results → done) inside that tick
    — and the first tick runs while the page waits for its handshake, so a
    page built here sees the end state. The progressive open has its own
    tests below."""
    cfg, work = _project(tmp_path)
    s = Shell(cfg, work_dir=work)
    s.session.open(cfg, work_dir=work)
    s.load.phase = "done"
    return s


async def _settled(predicate, tries: int = 60) -> None:
    """Events reach a page on the shell's tick (0.1 s); wait for one to land."""
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


def _suite(shell, user):
    """The Suite page object built for the user's CURRENT client. A page the
    user navigated away from is still registered — the simulation never
    deletes its client, only a browser disconnect does — so the shell may
    hold several."""
    from dstf.authoring.shell.suite import SuitePage

    (page,) = [
        p for p in shell.pages if isinstance(p, SuitePage) and p.client is user.client
    ]
    return page


# -- the header ------------------------------------------------------------


async def test_the_header_names_the_product_the_library_and_the_backend(shell):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        assert _one(user, "wordmark").text == "DSTF"
        assert _one(user, "project").text == "FakeLib"  # config.library_name
        backend = _one(user, "backend")
        assert backend.value == "Python" and backend.options == ["Python"]
        assert _one(user, "runs").text == "idle"


async def test_save_discard_and_export_say_their_object_effect_and_count(shell):
    """DESIGN.md "Buttons": the label names object, effect and count; at
    zero it still says what it would do and the sentence beside it says
    why it is disabled."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        save, discard, export = (_one(user, m) for m in ("save", "discard", "export"))
        assert save.text == "Save 0 spec edits (0 tests) to test_spec.json"
        assert not save.enabled and not discard.enabled and not export.enabled
        assert _one(user, "save:why").text == "no pending edits"
        assert _one(user, "pending").text == "0 pending edits"
        shell.session.stage([LOOSEN])
        await _settled(lambda: save.enabled)
        assert save.text == "Save 2 spec edits (1 test) to test_spec.json"
        assert discard.text == "Discard 2 edits" and discard.enabled
        assert export.text == "Export 2 edits to spec_patch.json" and export.enabled
        assert _one(user, "pending").text == "2 pending edits"
        # Hidden elements are not found by `find`; the why-line is read
        # through the page object.
        assert not _suite(shell, user).header.save.why.visible


async def test_export_discard_and_save_act_on_the_session_and_the_disk(shell):
    spec_path = shell.session.spec.path
    work_dir = shell.session.project.config.work_dir

    def entry_a() -> dict:
        tests = json.loads(spec_path.read_text(encoding="utf-8"))["tests"]
        return next(e for e in tests if e["model"] == A)

    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.session.stage([LOOSEN])
        await _settled(lambda: _one(user, "export").enabled)
        user.find(marker="export").click()
        envelope = json.loads(
            (work_dir / "spec_patch.json").read_text(encoding="utf-8")
        )
        assert [p["model"] for p in envelope["patches"]] == [A]
        user.find(marker="discard").click()
        await _settled(lambda: not shell.session.spec.pending)
        assert "metrics" not in entry_a()
        shell.session.stage([LOOSEN])
        await _settled(lambda: _one(user, "save").enabled)
        user.find(marker="save").click()
        await _settled(lambda: not shell.session.spec.pending)
        assert entry_a()["metrics"]["children"][0]["tolerance"] == 0.5


async def test_save_refused_on_a_changed_file_offers_reload(shell):
    spec_path = shell.session.spec.path
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.session.stage([LOOSEN])
        await _settled(lambda: _one(user, "save").enabled)
        spec_path.write_text(
            spec_path.read_text(encoding="utf-8") + "\n", encoding="utf-8"
        )
        user.find(marker="save").click()
        await user.should_see("changed on disk")
        assert shell.session.spec.pending  # kept
        user.find("Reload").click()
        await _settled(lambda: not shell.session.spec.pending)


async def test_stop_server_confirms_with_its_reasons_and_stops_on_the_second_word(
    shell, monkeypatch
):
    """Brief §3.3: Stop server confirms when edits are pending or a run is in
    flight, naming both."""
    stopped: list[bool] = []
    monkeypatch.setattr(app, "shutdown", lambda: stopped.append(True))
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.session.stage([LOOSEN])
        gate = _Gate()
        shell.session.apply(Job(run=gate, label="rerun", models=frozenset({D})))
        await _settled(lambda: _one(user, "pending").text == "2 pending edits")
        user.find(marker="stop").click()
        await user.should_see("2 unsaved edits will be lost")
        await user.should_see("rerun is still running")
        assert stopped == []
        user.find("Keep running").click()
        user.find(marker="stop").click()
        await user.should_see("Stop anyway")
        user.find(marker="stop-anyway").click()
        assert stopped == [True]
        gate.release.set()
        await _settled(lambda: shell.session.running is None)


async def test_stop_server_stops_at_once_with_nothing_to_lose(shell, monkeypatch):
    stopped: list[bool] = []
    monkeypatch.setattr(app, "shutdown", lambda: stopped.append(True))
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        user.find(marker="stop").click()
        assert stopped == [True]
        await user.should_not_see("Stop the server?")


async def test_the_pending_drawer_lists_staged_ops_by_model(shell):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        await user.should_see("nothing staged")
        shell.session.stage([LOOSEN])
        await user.should_see(
            marker="drawer-op", retries=20
        )  # `find` raises on absence
        await _settled(lambda: len(user.find(marker="drawer-op").elements) == 2)
        # `find` hands back a SET; the drawer draws the ops in staging order,
        # which is ascending element id.
        ops = sorted(user.find(marker="drawer-op").elements, key=lambda e: e.id)
        texts = [e.text for e in ops]
        assert texts[0].startswith("add /metrics ")
        assert texts[1].startswith("add /metrics/children/0/tolerance 0.5")
        assert _one(user, "drawer-model").text == A


async def test_the_dashboard_route_serves_the_open_projects_work_dir(shell):
    work_dir = shell.session.project.config.work_dir
    (work_dir / "dashboard.html").write_text("<h1>dash</h1>", encoding="utf-8")
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        ok = await user.http_client.get("/dashboard/dashboard.html")
        assert ok.status_code == 200 and "dash" in ok.text
        missing = await user.http_client.get("/dashboard/nothing.html")
        assert missing.status_code == 404


# -- the progressive open --------------------------------------------------


def _gate_the_read(monkeypatch) -> threading.Event:
    """`Backend.read_last_results` hands each result on only once the gate is
    set, so the open's in-between state — project set, nothing scored — is
    observable."""
    from dstf.backends.base import Backend

    real = Backend.read_last_results
    gate = threading.Event()

    def gated(self, tests, on_result=None):
        def slow(model_id, result):
            assert gate.wait(10)
            if on_result is not None:
                on_result(model_id, result)

        return real(self, tests, slow)

    monkeypatch.setattr(Backend, "read_last_results", gated)
    return gate


async def test_the_page_appears_while_the_project_opens_and_says_where_it_stands(
    tmp_path, monkeypatch
):
    """Brief §3.8: `open_async` runs the Session's open on a thread; the page
    is built before a result is read, and its progress line follows the
    loading events."""
    gate = _gate_the_read(monkeypatch)
    cfg, work = _project(tmp_path)
    shell = Shell(cfg, work_dir=work)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        assert _one(user, "project").text == "no project"
        await user.should_see("dstf author --config")  # the hint, until something opens
        shell.open_async()
        await _settled(lambda: shell.session.project is not None)
        progress = _suite(shell, user).progress  # hidden while empty: not `find`able
        await _settled(lambda: progress.text == "scoring results… 0 of 5")
        assert (
            _one(user, "project").text == "FakeLib"
        )  # the header follows the spec event
        assert shell.load.in_progress
        gate.set()
        await _settled(lambda: shell.load.phase == "done")
        await _settled(lambda: progress.text == "")
        assert {A, B, C, D, G} <= set(shell.session.comparisons)
        assert shell.load.error is None


async def test_an_open_that_fails_says_so_on_the_page(tmp_path):
    """Decision 8: a backend this machine cannot build (here a name nobody
    registered) leaves no project; the page says why and keeps the hint."""
    cfg, work = _project(tmp_path)
    shell = Shell(cfg, work_dir=work)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.open_async("NoSuchBackend")
        await _settled(lambda: shell.load.phase == "failed")
        await user.should_see("could not open the project", retries=20)
        await user.should_see("NoSuchBackend")
        await user.should_see("dstf author --config")
        assert shell.session.project is None


async def test_a_partial_scoring_failure_opens_the_project_with_a_warning(
    tmp_path, monkeypatch
):
    """Task 1's `done` in a `finally`, seen from the shell: the project is
    open, the failed model is on the shell's list of unscored models, and
    the failure is a warning line — not a failed open."""
    import dstf.authoring.session as session_mod

    real = session_mod.compare_one

    def failing(test, *args, **kwargs):
        if test.model_id == D:
            raise ValueError("boom")
        return real(test, *args, **kwargs)

    monkeypatch.setattr(session_mod, "compare_one", failing)
    cfg, work = _project(tmp_path)
    shell = Shell(cfg, work_dir=work)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.open_async()
        await _settled(
            lambda: shell.load.phase == "done" and shell.load.error is not None
        )
        assert shell.session.project is not None
        assert D not in shell.session.comparisons and shell.load.failed_models == [D]
        await user.should_see(
            "warning: RuntimeError: scoring FakeLib.D failed", retries=20
        )


# -- the runs card --------------------------------------------------------


async def test_a_thread_posted_completion_reaches_the_page(shell):
    """The bridge: Session(post=shell.post) queues the worker's completion;
    the page's timer drains it inside the page's context."""
    from dstf.authoring.capabilities import Job

    async with user_simulation(root=shell.index) as user:
        await _open(user)
        gate = _Gate()
        shell.session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
        await user.should_see("running: rerun", retries=20)
        gate.release.set()
        # the drained completion cleared it
        await user.should_see("no job running", retries=20)


async def test_a_rescore_after_the_open_does_not_put_the_shell_back_to_loading(shell):
    """`Session._reload_results` announces `loading(phase="results")` per
    model on EVERY rescore — a finished job's, a store write's — and only
    `open` closes with `done`. The shell reads those announcements only
    while an open is in progress: read after it, the first Rerun or Accept
    left `load` at "scoring results… 5 of 5" for good, and Save, Discard and
    the backend select stayed disabled with "the project is still loading"
    (found by the Task 5 review's probe)."""
    from dstf.authoring.capabilities import Job, StoreWrite

    async with user_simulation(root=shell.index) as user:
        await _open(user)
        gate = _Gate()
        shell.session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
        await user.should_see("running: rerun", retries=20)
        gate.release.set()
        await user.should_see("no job running", retries=20)
        assert not shell.load.in_progress and shell.busy_reason() is None
        assert shell.load.failed_models == []
        shell.session.apply(
            StoreWrite(run=lambda store: "stored 1", models=frozenset({B}))
        )
        await user.should_see("stored 1", retries=20)
        assert not shell.load.in_progress and shell.busy_reason() is None
        backend = _one(user, "backend")
        await _settled(lambda: backend.enabled)
        shell.session.stage([LOOSEN])  # a backend change then waits on the edits
        save = _one(user, "save")
        await _settled(lambda: save.enabled)


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

    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.session.apply(
            Job(run=write_status_then_wait, label="rerun", models=frozenset({A}))
        )
        await user.should_see("running: rerun", retries=20)
        # The poll is 1 s; should_see's default budget is 0.3 s.
        await user.should_see("simulating", retries=20)
        gate.release.set()
        await user.should_see("finished: rerun", retries=20)
        await user.should_see("no job running", retries=20)
        await user.should_not_see("simulating", retries=20)
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
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
        # The `started` event refreshes `jobs`, so `_progress_rows` has
        # already run against the stale file by the time this returns: the
        # absence is the guard's doing, not a timer that has yet to fire.
        await user.should_see("running: rerun", retries=20)
        await user.should_not_see("failed", retries=20)
        # Still the stale file: the gated job wrote no snapshot of its own.
        assert (
            json.loads((work / "status.json").read_text(encoding="utf-8"))["start_wall"]
            < shell.session.running.started_wall
        )
        gate.release.set()
        await user.should_see("no job running", retries=20)


async def test_a_job_that_raises_shows_its_failure_and_the_message(shell):
    """§4.7 "Failure handling": the runs card carries the message."""
    from dstf.authoring.capabilities import Job

    def boom(ctx):
        raise RuntimeError("no omc")

    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.session.apply(Job(run=boom, label="rerun", models=frozenset({A})))
        await user.should_see("failed: rerun", retries=20)
        await user.should_see("no omc", retries=20)
        assert shell.session.last_job is not None
        assert isinstance(shell.session.last_job.error, RuntimeError)


async def test_a_store_writes_line_is_shown_in_the_runs_card(shell):
    from dstf.authoring.capabilities import StoreWrite

    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.session.apply(
            StoreWrite(run=lambda store: "stored 1", models=frozenset({B}))
        )
        await user.should_see("stored 1", retries=20)
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

    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.session.apply(
            Job(run=fail_a_then_wait, label="rerun", models=frozenset({A}))
        )
        await user.should_see("running: rerun", retries=20)
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
    shell = Shell(cfg, work_dir=work)
    shell.session.open(cfg, work_dir=work)
    shell.load.phase = "done"
    assert shell.session.project.results[A].error_message == "OLD FAILURE"

    def boom(ctx):
        raise RuntimeError("no omc")

    async with user_simulation(root=shell.index) as user:
        await _open(user)
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
    shell = Shell(cfg, work_dir=work)
    shell.session.open(cfg, work_dir=work)
    shell.load.phase = "done"
    assert shell.session.project.results[A].error_message == "OLD FAILURE"

    async with user_simulation(root=shell.index) as user:
        await _open(user)
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
    the runs card standing — the notice BELOW the rows is what proves a raise
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
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.session.apply(
            StoreWrite(run=lambda store: "stored 1", models=frozenset({B}))
        )
        await user.should_see("stored 1", retries=20)
        shell.session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
        await user.should_see("running: rerun", retries=20)
        for make_status in malformed:
            # A good snapshot first, so the row's disappearance proves the
            # card was rebuilt against the bad one and not left as it was.
            _write_status(work, start_wall=time.time(), rows=[good_row])
            _suite(shell, user).runs.refresh()
            await user.should_see("simulating", retries=20)
            (work / "status.json").write_text(
                json.dumps(make_status(time.time())), encoding="utf-8"
            )
            _suite(shell, user).runs.refresh()
            await user.should_not_see("simulating", retries=20)
            await user.should_see("running: rerun", retries=20)
            await user.should_see("stored 1", retries=20)  # nothing below was lost
        gate.release.set()
        await user.should_see("no job running", retries=20)


async def test_the_last_jobs_outcome_is_the_projects_and_goes_with_it(shell):
    """The last job is the Session's, not the shell's: LibA's `finished:`
    line must not sit under "no project open" (nor under LibB after a swap)."""
    from dstf.authoring.capabilities import Job

    gate = _Gate()
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
        await user.should_see("running: rerun", retries=20)
        gate.release.set()
        await user.should_see("finished: rerun", retries=20)
        shell.session.close()
        await user.should_see("no project open", retries=20)
        await user.should_not_see("finished: rerun", retries=20)
        assert shell.session.last_job is None


# -- the backend -----------------------------------------------------------


def _with_alt_backend(cfg) -> None:
    """A second entry in the `backends` map that resolves to the same family:
    `Config.backend` maps "Python (alt)" to "Python", so the reopen is
    observable — a new Config, the select's new value — without a second
    simulator on this machine."""
    data = json.loads(cfg.read_text(encoding="utf-8"))
    data["backends"]["Python (alt)"] = ["python3", "python"]
    cfg.write_text(json.dumps(data), encoding="utf-8")


async def test_changing_the_backend_reopens_the_project_on_it(tmp_path):
    cfg, work = _project(tmp_path)
    _with_alt_backend(cfg)
    shell = Shell(cfg, work_dir=work)
    shell.session.open(cfg, work_dir=work)
    shell.load.phase = "done"
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        backend = _one(user, "backend")
        assert backend.options == ["Python", "Python (alt)"]
        backend.set_value("Python (alt)")
        await _settled(
            lambda: shell.load.phase == "done" and not shell.load.in_progress
        )
        await _settled(
            lambda: shell.session.project.config.backend_name == "Python (alt)"
        )
        await _settled(lambda: backend.value == "Python (alt)")
        assert shell.load.error is None
        # The same family reads the same partition: the verdicts are the old ones.
        assert classify(shell.session.comparisons[A]) == FAIL


async def test_the_backend_select_is_greyed_with_its_sentence_while_edits_are_pending(
    shell,
):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        assert _one(user, "backend").enabled
        shell.session.stage([LOOSEN])
        await _settled(lambda: not _one(user, "backend").enabled)
        assert (
            _one(user, "backend:why").text
            == "backend change waits: save or discard 2 pending edits first"
        )
        shell.session.discard()
        await _settled(lambda: _one(user, "backend").enabled)


async def test_a_backend_that_cannot_be_built_leaves_the_project_and_says_why(shell):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.open_async("NoSuchBackend")
        await _settled(
            lambda: shell.load.phase == "done" and shell.load.error is not None
        )
        assert shell.session.project.config.backend_name == "Python"  # as it was
        assert _one(user, "backend").value == "Python"
        await user.should_see("warning:", retries=20)
        await user.should_see("NoSuchBackend")


class _FakeProc:
    def __init__(self, cmd, calls):
        self.cmd, self.calls = cmd, calls
        calls.append(cmd)

    def poll(self):
        return None

    def terminate(self):
        self.calls.append("terminated")


async def test_open_in_another_backend_starts_a_second_process_on_a_free_port(
    tmp_path, monkeypatch
):
    """Brief §3.6: one project and backend per process. The child gets this
    process's config, the chosen backend, a free port, and its `--work-dir`
    (decision 6); it opens its own browser tab; Stop server ends it
    (decision 7)."""
    cfg, work = _project(tmp_path)
    _with_alt_backend(cfg)
    shell = Shell(cfg, work_dir=work)
    shell.session.open(cfg, work_dir=work)
    shell.load.phase = "done"
    calls: list = []
    import dstf.authoring.shell.shell as shell_mod

    monkeypatch.setattr(
        shell_mod.subprocess, "Popen", lambda cmd: _FakeProc(cmd, calls)
    )
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        user.find(marker="other-backend").click()
        await user.should_see("Open this project in another backend")
        _one(user, "other-choice").set_value("Python (alt)")
        user.find(marker="start-other").click()
        await user.should_see("open http://127.0.0.1:", retries=20)
    cmd = calls[0]
    assert cmd[:3] == [sys.executable, "-m", "dstf"]
    assert cmd[cmd.index("--config") + 1] == str(cfg)
    assert cmd[cmd.index("author") + 1 :][:2] == ["--backend", "Python (alt)"]
    assert cmd[cmd.index("--work-dir") + 1] == str(work)
    port = int(cmd[cmd.index("--port") + 1])
    assert 1024 < port < 65536
    assert "--no-browser" not in cmd  # the child opens its own tab
    shell.terminate_children()
    assert calls[-1] == "terminated"


def test_a_no_browser_parent_starts_no_browser_children_without_a_work_dir(
    tmp_path, monkeypatch
):
    cfg, _work = _project(tmp_path)
    calls: list = []
    import dstf.authoring.shell.shell as shell_mod

    monkeypatch.setattr(
        shell_mod.subprocess, "Popen", lambda cmd: _FakeProc(cmd, calls)
    )
    shell = Shell(cfg, show_browser=False, filter="FakeLib.A*", package="FakeLib")
    url = shell.spawn("Python")
    cmd = calls[0]
    assert url.startswith("http://127.0.0.1:") and url.endswith("/")
    assert "--no-browser" in cmd and "--work-dir" not in cmd
    assert cmd[cmd.index("--filter") + 1] == "FakeLib.A*"
    assert cmd[cmd.index("--package") + 1] == "FakeLib"
