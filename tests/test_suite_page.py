"""The Suite page's table, Python-side (brief §3.2, §3.8; the Suite table,
S2a): the rows Python sends the grid — every column's text and sort value,
from the row model — the selection it receives, kept in the tab across a
navigation, the icon actions' availability, and the grid refusing an
element update (NiceGUI's aggrid rebuilds the grid on one). The grid's rows,
filters, sort and groups live in the browser: `test_author_suite_playwright`
drives them. In NiceGUI's user simulation."""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("nicegui")

from nicegui.testing.user_simulation import user_simulation  # noqa: E402

from dstf.authoring import capabilities as caps  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.shell.shell import Shell  # noqa: E402
from dstf.authoring.shell.suite import SuitePage  # noqa: E402
from test_session import A, B, C, D, G, _project  # noqa: E402


@pytest.fixture(autouse=True)
def _registry():
    caps._reset_for_tests()
    register_builtins()
    yield
    caps._reset_for_tests()
    register_builtins()


@pytest.fixture
def shell(tmp_path):
    cfg, work = _project(tmp_path)
    s = Shell(cfg, work_dir=work)
    s.session.open(cfg, work_dir=work)
    s.load.phase = "done"
    return s


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
    """The Suite page built for the user's CURRENT client: a page the user
    navigated away from stays registered (the simulation never deletes its
    client), so the shell may hold several."""
    (page,) = [
        p for p in shell.pages if isinstance(p, SuitePage) and p.client is user.client
    ]
    return page


def short(model_id: str) -> str:
    return model_id.rsplit(".", 1)[-1]


def _sent(page) -> dict[str, dict]:
    """The rows the page built the grid with, by id (group rows are the
    browser's to add)."""
    return {r["id"]: r for r in page.grid.options["rowData"]}


async def test_the_rows_python_sends_carry_every_columns_text_and_sort(shell):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _suite(shell, user)
        rows = _sent(page)
        assert list(rows) == [A, B, C, D, G]  # the project's order
        a = rows[A]
        assert a["status"] == "FAIL" and a["status_class"] == "fail"
        assert a["status__sort"] is not None
        assert (a["_name_short"], a["_name_full"], a["_pkg"]) == ("A", A, "FakeLib")
        assert a["model"] == A  # the Test column filters on the full name
        assert a["nrmse__sort"] > 0.1 and a["nrmse"] == format(a["nrmse__sort"], ".4e")
        assert a["edits"] == "" and a["edits__sort"] is None
        assert {rows[m]["status"] for m in (D, G)} == {"PASS"}
        assert (rows[B]["status"], rows[C]["status"]) == ("NO_REF", "SIM_FAIL")
        # A text column with no value filters as empty (S1's filter_text).
        assert rows[B]["ref"] == "—" and rows[B]["ref__sort"] is None


async def test_the_grid_offers_the_tools_columns(shell):
    """The tool's columns: the row model's, bar Key / Worker / Elapsed (a
    live run's; the runs card shows a running job), plus Edits; the chooser
    offers the hidden ones."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        defs = _suite(shell, user).grid.options["columnDefs"]
        shown = [d["colId"] for d in defs if not d.get("hide")]
        assert shown[:2] == ["status", "model"]
        assert set(shown) == {
            "status",
            "model",
            "nrmse",
            "vars",
            "sim",
            "warnings",
            "detail",
            "edits",
        }
        hidden = {d["colId"] for d in defs if d.get("hide")}
        assert {"test", "worker", "elapsed", "ref", "translate", "total"} <= hidden
        labels = {d["colId"]: d["headerName"] for d in defs}
        assert (labels["model"], labels["test"], labels["edits"]) == (
            "Test",
            "Key",
            "Edits",
        )
        nrmse = next(d for d in defs if d["colId"] == "nrmse")
        assert nrmse["width"] >= 130  # "1.5309e-01" and its header at 1280


async def test_the_selection_the_grid_reports_is_the_tabs_and_the_actions(shell):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _suite(shell, user)
        rerun = page.actions.actions["rerun"].button
        assert not rerun.enabled
        page._on_selection([A, D])
        assert page.state.checked == {A, D}
        assert rerun.enabled
        assert rerun.props["aria-label"] == "Rerun 2 selected tests"
        page._on_selection([])
        assert page.state.checked == set() and not rerun.enabled


async def test_a_selection_survives_a_visit_to_a_test(shell):
    """Brief §3.2: the selection is the tab's, so it is there when the Test
    page hands the tab back: the new page's grid selects it once ready."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        first = _suite(shell, user)
        first._on_selection([D, G])
        await user.open(f"/test/{A}")
        await user.should_see("← Suite", retries=20)
        user.find("← Suite").click()
        await _settled(lambda: _suites(shell, user))
        second = _suite(shell, user)
        assert second is not first and second.state is first.state
        ready = second.grid.options[":onGridReady"]
        assert json.dumps([D, G]) in ready


def _suites(shell, user):
    return [
        p for p in shell.pages if isinstance(p, SuitePage) and p.client is user.client
    ]


async def test_a_sealed_grid_refuses_an_element_update(shell):
    """NiceGUI's aggrid re-creates the grid on any element update: sort,
    filters, selection and every patched row lost. `.classes` / `.props`
    reach `update` through an observable whose handler LOGS what it raises,
    so the guard blocks the update and the caller sees no raise; a direct
    `update()` raises."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _suite(shell, user)
        outbox = page.client.outbox
        outbox.updates.pop(page.grid.id, None)
        page.grid.classes(add="touched")
        assert page.grid.id not in outbox.updates
        with pytest.raises(RuntimeError, match="driven by run_grid_method"):
            page.grid.update()
