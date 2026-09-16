"""The Suite page's list (brief §3.2, §3.8; DESIGN.md "Tables"): grouped or
flat, short or full; the filter and the status chips; checkboxes that
collect and never change the view, a click that opens the Test page and
checks nothing; the checked set kept in the tab across that navigation;
rows that fill in as results are scored. In NiceGUI's user simulation."""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

pytest.importorskip("nicegui")

from nicegui.testing.user_simulation import user_simulation  # noqa: E402

from dstf.authoring import capabilities as caps  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.shell.shell import Shell  # noqa: E402
from dstf.authoring.shell.suite import SuitePage  # noqa: E402
from dstf.reporting.status import count_by_status  # noqa: E402
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


async def test_the_list_is_grouped_with_short_names_and_every_status(shell):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        assert _one(user, "group:FakeLib").text == "FakeLib"
        page = _suite(shell, user)
        for model_id, verdict in (
            (A, "FAIL"),
            (B, "NO_REF"),
            (C, "SIM_FAIL"),
            (D, "PASS"),
            (G, "PASS"),
        ):
            assert _one(user, model_id).text == short(model_id)
            assert page.rows[model_id].badge.text == verdict
            assert page.rows[model_id].verdict == verdict


async def test_flat_and_full_names_are_a_toggle_away(shell):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        _one(user, "naming").set_value(False)
        await _settled(lambda: _one(user, A).text == A)
        assert _suite(shell, user).state.short is False
        _one(user, "grouping").set_value(False)
        await user.should_not_see(marker="group:FakeLib", retries=20)
        assert [r for r in _suite(shell, user).rows] == [A, B, C, D, G]  # project order


async def test_a_click_opens_the_test_page_and_checks_nothing(shell):
    """Brief §3.2: viewing and selecting are separate."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _suite(shell, user)
        user.find(marker=A).click()
        await user.should_see("Plots, criteria and the Variables drawer", retries=20)
        assert _one(user, "model").text == A
        assert _one(user, "status").text == "FAIL"
        assert page.state.checked == set()


async def test_checks_collect_without_changing_the_view_and_survive_the_navigation(
    shell,
):
    """Brief §3.2: checkboxes only collect; selection state is kept per
    browser tab, so it is there when the Test page hands the tab back."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        first = _suite(shell, user)
        user.find(marker=f"check:{D}").click()
        user.find(marker=f"check:{A}").click()
        assert first.state.checked == {A, D}
        # A navigation from a handler is a background task that runs at the
        # next await: give it one, then assert the client did not move.
        await asyncio.sleep(0.2)
        assert user.client is first.client  # still the Suite page
        user.find(marker=A).click()
        await user.should_see("← Suite", retries=20)
        user.find("← Suite").click()
        await user.should_see("Check", retries=20)
        second = _suite(shell, user)  # the page built for the new client
        assert second is not first  # a new page object for the new build
        assert second.state is first.state  # the same tab's state
        assert _one(user, f"check:{D}").value is True
        assert _one(user, f"check:{A}").value is True
        assert _one(user, f"check:{B}").value is False


async def test_the_filter_and_the_chips_narrow_the_list_without_resending_it(shell):
    """The row model: a filter hides rows, it does not rebuild them — the
    element ids are the ones the page was built with — and the Session is
    not consulted."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _suite(shell, user)
        ids = {m: row.container.id for m, row in page.rows.items()}
        comparisons = dict(shell.session.comparisons)
        _one(user, "needle").set_value("d")
        assert page.rows[D].container.visible and not page.rows[A].container.visible
        assert {m: row.container.id for m, row in page.rows.items()} == ids
        _one(user, "needle").set_value("")
        user.find(marker="chip:PASS").click()
        assert page.state.status_filter == {"PASS"}
        assert page.rows[D].container.visible and page.rows[G].container.visible
        assert not page.rows[A].container.visible and not page.rows[B].container.visible
        user.find(marker="chip:PASS").click()  # off again
        assert page.state.status_filter == set() and page.rows[A].container.visible
        assert {m: row.container.id for m, row in page.rows.items()} == ids
        assert shell.session.comparisons == comparisons


async def test_a_filtered_out_row_stays_hidden_through_a_rescore(shell):
    """A repaint must not undo the filter. NiceGUI hides an element by
    appending a `hidden` class; a paint that REPLACED the row's whole class
    list dropped it, and the following `set_visibility(False)` was a no-op
    on a value that was already False — so every filtered-out row came back
    in the browser on any rescore (a Rerun, an Accept), while "Check N shown
    tests" still excluded them (the Task 6 review's probe). The class list
    is the witness, not `visible`."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _suite(shell, user)
        _one(user, "needle").set_value("d")
        assert not page.rows[A].container.visible
        assert "hidden" in page.rows[A].container.classes
        page.handle(
            {
                "event": "loading",
                "phase": "results",
                "model": A,
                "done": 1,
                "total": 5,
                "error": False,
            }
        )
        page.handle({"event": "results", "models": [A, B, C, D, G]})
        assert not page.rows[A].container.visible
        assert "hidden" in page.rows[A].container.classes
        assert (
            page.rows[D].container.visible
            and "hidden" not in page.rows[D].container.classes
        )
        assert (
            "dstf-tint-fail" in page.rows[A].container.classes
        )  # the paint still landed


async def test_the_chips_carry_the_classifiers_counts(shell):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        counts = count_by_status(list(shell.session.comparisons.values()))
        for status in ("PASS", "FAIL", "SIM_FAIL", "NO_REF"):
            assert _one(user, f"chip:{status}").text == f"{status} {counts[status]}"


async def test_check_shown_and_uncheck_say_their_counts_and_explain_zero(shell):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _suite(shell, user)
        assert _one(user, "check-shown").text == "Check 5 shown tests"
        assert _one(user, "uncheck").text == "Uncheck 0 tests"
        assert not _one(user, "uncheck").enabled
        assert _one(user, "uncheck:why").text == "nothing is checked"
        _one(user, "needle").set_value("d")
        await _settled(lambda: _one(user, "check-shown").text == "Check 1 shown test")
        user.find(marker="check-shown").click()
        assert page.state.checked == {D}
        _one(user, "needle").set_value("")
        await _settled(lambda: _one(user, "uncheck").text == "Uncheck 1 test")
        assert _one(user, f"check:{D}").value is True
        user.find(marker="uncheck").click()
        assert page.state.checked == set()
        await _settled(lambda: _one(user, f"check:{D}").value is False)


def _gate_per_model(monkeypatch) -> dict[str, threading.Event]:
    """Each result is handed on when ITS gate is set, so rows can be seen
    filling in one at a time. The reader hands its results on in its thread
    pool's completion order (`Backend.read_results` iterates `as_completed`),
    which is not this test's order, so the results are collected first and
    released, on the reader's thread, as their gates open."""
    from dstf.backends.base import Backend

    real = Backend.read_last_results
    gates: dict[str, threading.Event] = {m: threading.Event() for m in (A, B, C, D, G)}

    def gated(self, tests, on_result=None):
        held: dict[str, object] = {}
        results = real(self, tests, lambda m, r: held.__setitem__(m, r))
        deadline = time.monotonic() + 10
        while held:
            assert time.monotonic() < deadline
            for model_id in list(held):
                if gates[model_id].is_set():
                    result = held.pop(model_id)
                    if on_result is not None:
                        on_result(model_id, result)
            time.sleep(0.01)
        return results

    monkeypatch.setattr(Backend, "read_last_results", gated)
    return gates


async def test_rows_fill_in_as_results_are_scored(tmp_path, monkeypatch):
    """Brief §3.8: the list fills in as tests are discovered and status
    badges as results are scored — per `loading` event, not at the end."""
    gates = _gate_per_model(monkeypatch)
    cfg, work = _project(tmp_path)
    shell = Shell(cfg, work_dir=work)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _suite(shell, user)
        shell.open_async()
        await _settled(lambda: A in page.rows)
        assert {m: page.rows[m].badge.text for m in (A, B, C, D, G)} == dict.fromkeys(
            (A, B, C, D, G), "…"
        )
        gates[A].set()
        await _settled(lambda: page.rows[A].badge.text == "FAIL")
        assert page.rows[D].badge.text == "…"  # still gated
        for gate in gates.values():
            gate.set()
        await _settled(lambda: shell.load.phase == "done")
        await _settled(lambda: page.rows[D].badge.text == "PASS")
        assert page.rows[C].badge.text == "SIM_FAIL"


async def test_a_model_whose_scoring_raised_reads_scoring_failed(tmp_path, monkeypatch):
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
        await _settled(lambda: shell.load.phase == "done")
        await _settled(
            lambda: _suite(shell, user).rows[D].badge.text == "scoring failed"
        )
        assert _suite(shell, user).rows[A].badge.text == "FAIL"


async def test_a_changed_test_set_rebuilds_the_rows(shell):
    """A model the spec newly names (here by editing the file and reloading,
    the rescan's and Save's path) gets a row; the others keep theirs."""
    spec_path = shell.session.spec.path
    (spec_path.parent.parent / "Examples" / "E.py").write_text(
        "def simulate(stop_time, tolerance):\n    return {}\n", encoding="utf-8"
    )
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _suite(shell, user)
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec["tests"].append(
            {
                "model": "FakeLib.E",
                "source": "../Examples/E.py",
                "variables": ["x"],
                "simulation": {"stop_time": 1.0, "tolerance": 1e-6},
            }
        )
        spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        shell.session.reload()
        await _settled(lambda: "FakeLib.E" in page.rows)
        assert page.rows["FakeLib.E"].badge.text == "SIM_FAIL"  # no result
        assert set(page.rows) == {A, B, C, D, G, "FakeLib.E"}
