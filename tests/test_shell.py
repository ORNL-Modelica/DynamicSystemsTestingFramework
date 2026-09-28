"""The new shell (roadmap §2; D129) in NiceGUI's in-process user simulation,
no browser: the process (`Shell`), the header, Save / Discard / Export, Stop
server, the pending drawer, the dashboard route, the progressive open and
the runs card. The Suite list is tests/test_suite_page.py; the buttons at a
placement tests/test_capability_conformance.py."""

from __future__ import annotations

import asyncio
import gc
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
from dstf.authoring.spec_document import PatchOp  # noqa: E402
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
    """The backend is text in the row, with its OS partition; its select is
    in the Change backend dialog (the owner's ruling, Q13 A)."""
    os_name = shell.session.project.config.os_name
    assert os_name
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        assert _one(user, "wordmark").text == "DSTF"
        assert _one(user, "project").text == "FakeLib"  # config.library_name
        assert _one(user, "backend-name").text == f"Python · {os_name}"
        backend = _one(user, "backend")
        assert backend.value == "Python" and backend.options == ["Python"]
        assert _one(user, "runs").text == "idle"


def _last_run(work, **metadata) -> None:
    """A status.json whose run provenance is *metadata*."""
    (work / "status.json").write_text(
        json.dumps({"total": 0, "counts": {}, "tests": [], "metadata": metadata}),
        encoding="utf-8",
    )


#: 2026-09-27 12:00 local time, as the tooltip prints it.
WHEN = time.mktime((2026, 9, 27, 12, 0, 0, 0, 0, -1))


async def test_the_header_names_the_last_runs_tool_version(tmp_path):
    """The header names the tool's version the last run recorded, short
    ("Dymola 2026x"), and its tooltip says it is the LAST RUN's, in full.
    FakeLib opens a Python backend only: the short form is keyed on the
    version string, so a Dymola string is what shows one."""
    cfg, work = _project(tmp_path)
    _last_run(
        work,
        backend_name="Python",
        tool_version="Dymola Version 2026x, 2025-10-10",
        library_versions={"Modelica": "4.1.0"},
        os="linux",
        generated_at=WHEN,
    )
    s = Shell(cfg, work_dir=work)
    s.session.open(cfg, work_dir=work)
    s.load.phase = "done"
    os_name = s.session.project.config.os_name
    async with user_simulation(root=s.index) as user:
        await _open(user)
        assert _one(user, "backend-name").text == f"Dymola 2026x · {os_name}"
        assert _one(user, "backend-name:tip").text == (
            "Last run: Dymola Version 2026x, 2025-10-10 · MSL 4.1.0 · linux"
            " · 2026-09-27 12:00"
        )


async def test_the_header_names_a_backend_that_has_not_run_by_its_name(tmp_path):
    """When the configured backend is not the one the last run recorded,
    its version is unknown until it runs: the header reads its name, and
    the tooltip says so beside the last run's."""
    cfg, work = _project(tmp_path)
    _last_run(
        work,
        backend_name="Dymola",
        tool_version="Dymola Version 2026x, 2025-10-10",
        os="linux",
        generated_at=WHEN,
    )
    s = Shell(cfg, work_dir=work)
    s.session.open(cfg, backend="Python", work_dir=work)
    s.load.phase = "done"
    os_name = s.session.project.config.os_name
    async with user_simulation(root=s.index) as user:
        await _open(user)
        assert _one(user, "backend-name").text == f"Python · {os_name}"
        assert _one(user, "backend-name:tip").text == (
            "Python has not run here: its version is known once it runs. "
            "Last run: Dymola Version 2026x, 2025-10-10 · linux · 2026-09-27 12:00"
        )


async def test_the_header_without_a_recorded_version_names_the_backend(tmp_path):
    """A last run that recorded no version (the fixture's status.json names
    only the backend) reads the backend's name, the tooltip naming the last
    run by it; with no status.json at all the tooltip says no run recorded
    a version."""
    for recorded, tip in (
        (True, "Last run: Python"),
        (False, "No run here has recorded the tool's version yet"),
    ):
        cfg, work = _project(tmp_path / str(recorded))
        if not recorded:
            (work / "status.json").unlink()
        s = Shell(cfg, work_dir=work)
        s.session.open(cfg, work_dir=work)
        s.load.phase = "done"
        os_name = s.session.project.config.os_name
        async with user_simulation(root=s.index) as user:
            await _open(user)
            assert _one(user, "backend-name").text == f"Python · {os_name}"
            assert _one(user, "backend-name:tip").text == tip


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
        # Export is an item of the header's menu (3c): a `ui.menu_item` has
        # no `.text`, and its caption is a label of its own.
        assert _suite(shell, user).header.export_text.text == (
            "Export 2 edits to spec_patch.json"
        )
        assert export.enabled
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


def _lines(user, kind: str) -> list[str]:
    """The drawer's diff lines of one kind, in drawing order (ascending id),
    each as its text reads, spaces collapsed."""
    try:
        found = user.find(marker=f"diff-{kind}").elements
    except AssertionError:
        return []
    return [
        " ".join("".join(c.text for c in e.default_slot.children).split())
        for e in sorted(found, key=lambda e: e.id)
    ]


def _saved_tree(shell) -> None:
    """A's entry holds a tree with a tolerance ON DISK: LOOSEN (0.5) saved."""
    shell.session.stage([LOOSEN])
    shell.session.save()
    assert shell.session.spec.pending == []


#: On the saved tree: 0.5 → 0.1 tightens A past its nrmse of 0.153.
TIGHTEN = PatchOp(A, "replace", "/metrics/children/0/tolerance", 0.1)


def _open_drawer(user) -> None:
    """The preview is built on opening, and while open."""
    user.find(marker="pending").click()


async def test_the_pending_drawer_previews_each_tests_entry(shell):
    """Q6 A: per test, the entry before and after as `json.dumps(indent=2)`
    writes it, removed lines and added lines, how many edits and what Save
    does to its verdict. The raw op lines are gone."""
    _saved_tree(shell)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        _open_drawer(user)
        await user.should_see("nothing staged")
        shell.session.stage([TIGHTEN])
        await user.should_see(marker=f"drawer-test:{A}", retries=20)
        assert _one(user, "drawer-model").text == A
        assert _one(user, "drawer-summary").text == "1 pending edit · 1 test"
        assert _one(user, "drawer-count").text == "1 edit"
        assert _one(user, "drawer-verdict").text == "verdict would change PASS → FAIL"
        assert _lines(user, "del") == ['- "tolerance": 0.5']
        assert _lines(user, "add") == ['+ "tolerance": 0.1']
        assert 0 < len(_lines(user, "same")) <= 6
        # The tree was saved: nothing is written out besides the edit.
        with pytest.raises(AssertionError):
            user.find(marker="drawer-note")
        with pytest.raises(AssertionError):
            user.find(marker="drawer-op")


async def test_a_first_edit_on_a_flat_test_diffs_against_its_tree_as_scored(shell):
    """A's entry is flat: its first per-leaf edit stages the implicit tree
    written out, then the edit. The diff is against that tree — it scores
    the same — so it shows the edit, not the whole tree added; a note says
    Save writes the tree too. The count is the ops', as the header's."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        _open_drawer(user)
        shell.session.stage([LOOSEN])
        await user.should_see(marker=f"drawer-test:{A}", retries=20)
        assert _one(user, "drawer-count").text == "2 edits"
        assert _one(user, "drawer-verdict").text == "verdict would change FAIL → PASS"
        assert _one(user, "drawer-note").text == (
            "Save also writes this test's 1 criterion out as a tree; it scores "
            "the same."
        )
        # difflib reads the leaf's last line gaining its comma as a changed
        # line, then the tolerance added after it.
        assert _lines(user, "del") == ['- "variable": "x"']
        assert _lines(user, "add") == ['+ "variable": "x",', '+ "tolerance": 0.5']


async def test_the_note_counts_the_criteria_the_tree_writes_out(tmp_path):
    """`_two_var_project`'s A tracks x and y: its tree has two criteria."""
    from test_session import _two_var_project

    cfg, work = _two_var_project(tmp_path)
    shell = Shell(cfg, work_dir=work)
    shell.session.open(cfg, work_dir=work)
    shell.load.phase = "done"
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        _open_drawer(user)
        shell.session.stage([LOOSEN])
        await user.should_see(marker="drawer-note", retries=20)
        assert _one(user, "drawer-note").text == (
            "Save also writes this test's 2 criteria out as a tree; they score "
            "the same."
        )


async def test_a_new_entrys_note_says_it_is_new(tmp_path):
    """D is scanned but the spec file has no entry for it: its first
    per-leaf edit creates the entry with its tree written out, and the diff
    is against that tree, as for a flat entry."""
    from dstf.authoring.session import Session

    cfg, work = _project(tmp_path)
    first = Session()
    first.open(cfg, work_dir=work)
    d_model = first.project.test(D)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["tests"] = [e for e in spec["tests"] if e["model"] != D]
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    shell = Shell(cfg, work_dir=work)
    shell.session.open(cfg, work_dir=work)
    shell.load.phase = "done"
    # Scanned: a recognizer would find it; FakeLib's Python source has none.
    # Re-read: the open read no result for a model that was no test.
    shell.session.project.scanned[D] = d_model
    shell.session._rebuild_tests({D}, reread=True)
    assert shell.session.spec.saved_entry(D) is None
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        _open_drawer(user)
        shell.session.stage([PatchOp(D, "add", "/metrics/children/0/tolerance", 0.5)])
        await user.should_see(marker="drawer-note", retries=20)
        assert _one(user, "drawer-note").text == (
            "A new entry for this test. Save also writes this test's 1 "
            "criterion out as a tree; it scores the same."
        )
        assert _lines(user, "add") == ['+ "variable": "x",', '+ "tolerance": 0.5']


def test_the_note_counts_its_criteria_and_says_a_new_entry():
    from dstf.authoring.shell.header import materialized_note

    assert materialized_note(3, new_entry=False) == (
        "Save also writes this test's 3 criteria out as a tree; they score the same."
    )
    assert materialized_note(1, new_entry=True) == (
        "A new entry for this test. Save also writes this test's 1 criterion "
        "out as a tree; it scores the same."
    )


async def test_the_drawer_shows_every_edited_test_in_staging_order(shell):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        _open_drawer(user)
        shell.session.stage([PatchOp(D, "add", "/comparison", {"tolerance": 0.5})])
        shell.session.stage([LOOSEN])
        await user.should_see(marker=f"drawer-test:{A}", retries=20)
        await user.should_see(marker=f"drawer-test:{D}", retries=20)
        models = sorted(user.find(marker="drawer-model").elements, key=lambda e: e.id)
        assert [e.text for e in models] == [D, A]
        # LOOSEN materializes A's tree: 2 ops, and D's 1, over 2 tests.
        assert _one(user, "drawer-summary").text == "3 pending edits · 2 tests"


async def test_save_these_writes_the_effective_entry_and_empties_the_drawer(shell):
    _saved_tree(shell)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        _open_drawer(user)
        shell.session.stage([TIGHTEN])
        await user.should_see(marker="drawer-save", retries=20)
        expected = json.dumps(shell.session.spec.effective(), indent=2) + "\n"
        user.find(marker="drawer-save").click()
        await user.should_see("nothing staged", retries=20)
        assert shell.session.spec.path.read_text(encoding="utf-8") == expected


async def test_save_these_refuses_a_file_changed_on_disk(shell):
    """The drawer's Save is the header's: the stale-file dialog, nothing
    written."""
    _saved_tree(shell)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        _open_drawer(user)
        shell.session.stage([TIGHTEN])
        await user.should_see(marker="drawer-save", retries=20)
        path = shell.session.spec.path
        path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        on_disk = path.read_bytes()
        user.find(marker="drawer-save").click()
        await user.should_see("changed on disk since it was loaded", retries=20)
        assert path.read_bytes() == on_disk
        assert shell.session.spec.pending == [TIGHTEN]


async def test_discard_these_empties_the_drawer(shell):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        _open_drawer(user)
        shell.session.stage([LOOSEN])
        await user.should_see(marker="drawer-discard", retries=20)
        user.find(marker="drawer-discard").click()
        await user.should_see("nothing staged", retries=20)
        assert shell.session.spec.pending == []


async def test_the_preview_is_built_only_while_the_drawer_is_open(shell, monkeypatch):
    """A closed drawer scores nothing; an event that moved nothing it shows
    — the 1 s poll during a job — builds nothing again."""
    calls: list[set] = []
    real = type(shell.session).saved_comparisons

    def counting(self, models):
        calls.append(set(models))
        return real(self, models)

    monkeypatch.setattr(type(shell.session), "saved_comparisons", counting)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.session.stage([LOOSEN])
        await asyncio.sleep(0.3)
        assert calls == []
        _open_drawer(user)
        await user.should_see(marker=f"drawer-test:{A}", retries=20)
        assert calls == [{A}]  # one call for every edited test
        diff = _one(user, "drawer-diff")
        shell._events.put({"event": "poll"})
        await asyncio.sleep(0.3)
        assert calls == [{A}]
        assert _one(user, "drawer-diff") is diff  # not rebuilt
        shell.session.stage([PatchOp(D, "add", "/comparison", {"tolerance": 0.5})])
        await user.should_see(marker=f"drawer-test:{D}", retries=20)
        assert calls[-1] == {A, D}


async def test_the_open_drawer_follows_a_rescore_that_moved_the_verdict(shell):
    """A rerun's reload rescores A with nothing pending moved: the drawer's
    key holds the comparison, and the verdict line follows it."""
    import numpy as np

    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.session.stage([LOOSEN])  # A: FAIL at 0.01, PASS loosened
        _open_drawer(user)
        await user.should_see(marker="drawer-verdict", retries=20)
        assert _one(user, "drawer-verdict").text == "verdict would change FAIL → PASS"
        for v in shell.session.project.results[A].variables:
            v.values = np.asarray(v.values) * 10.0  # a rerun's result, far off
        shell.session._score_all({A})  # the reload's rescore: one `results` event
        await _settled(
            lambda: _one(user, "drawer-verdict").text == "verdict stays FAIL"
        )


async def test_a_reopened_drawer_is_built_again(shell, monkeypatch):
    """A closed drawer forgets what it drew: a comparison freed while it
    was closed can come back at the address its key recorded, and the
    reopened drawer would show the verdict it had."""
    from dstf.authoring.shell.header import PendingDrawer

    builds: list[int] = []
    real = PendingDrawer._build

    def counting(self):
        builds.append(1)
        real(self)

    monkeypatch.setattr(PendingDrawer, "_build", counting)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.session.stage([LOOSEN])
        _open_drawer(user)
        await user.should_see(marker=f"drawer-test:{A}", retries=20)
        user.find(marker="pending").click()  # close
        await asyncio.sleep(0.3)
        _open_drawer(user)
        await _settled(lambda: len(builds) == 2)


async def test_the_open_drawer_holds_every_comparison_its_key_names(shell):
    """The key names each edited test's comparison by id; while it does,
    the drawer holds that comparison, so a rescore that replaced it cannot
    free it and bring a new one back at its address. Closed, it holds
    nothing."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.session.stage([LOOSEN])
        _open_drawer(user)
        await user.should_see(marker=f"drawer-test:{A}", retries=20)
        drawer = _suite(shell, user).header.drawer
        recorded = [i for _, i in drawer.drawn[2]]
        # No await: nothing refreshes the drawer after this rescore.
        shell.session._score(A)
        gc.collect()
        assert [id(c) for c in drawer.held] == recorded
        user.find(marker="pending").click()  # close
        await _settled(lambda: drawer.held == [])


async def test_a_rescored_flat_test_shows_its_note_and_no_empty_diff(shell):
    """`rescore` writes A's tree out and nothing else: the section says
    Save writes the tree, and draws no empty diff box."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.session.rescore(A)
        _open_drawer(user)
        await user.should_see(marker="drawer-note", retries=20)
        with pytest.raises(AssertionError):
            user.find(marker="drawer-diff")


async def test_the_drawers_save_and_discard_wait_like_the_headers(shell):
    """ "Save these" / "Discard these" are the header's Save and Discard:
    greyed with the same sentence while a job runs or the project is still
    being read — and a click then writes nothing."""
    path = shell.session.spec.path
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.session.stage([LOOSEN])
        _open_drawer(user)
        await user.should_see(marker=f"drawer-test:{A}", retries=20)
        await _settled(lambda: _one(user, "drawer-save").enabled)
        assert _one(user, "drawer-discard").enabled
        gate = _Gate()
        shell.session.apply(
            Job(run=gate, label="Probe", models=frozenset({B}), reloads_results=False)
        )
        await _settled(lambda: not _one(user, "drawer-save").enabled)
        assert not _one(user, "drawer-discard").enabled
        assert _one(user, "drawer-save:why").text == "Probe is running"
        gate.release.set()
        await _settled(lambda: shell.session.running is None)
        await _settled(lambda: _one(user, "drawer-save").enabled)
        shell.load.phase = "results"
        shell._events.put({"event": "poll"})
        await _settled(lambda: not _one(user, "drawer-save").enabled)
        assert not _one(user, "drawer-discard").enabled
        assert _one(user, "drawer-save:why").text == shell.busy_reason()
        before = path.read_bytes()
        user.find(marker="drawer-save").click()
        user.find(marker="drawer-discard").click()
        await asyncio.sleep(0.3)
        assert path.read_bytes() == before
        assert len(shell.session.spec.pending) == 2


async def test_a_saved_verdict_that_raises_says_so_for_its_test_alone(
    shell, monkeypatch, caplog
):
    """Scoring D's SAVED spec raises (its edit fixes it): D's section says
    the saved verdict failed, A's reads as ever."""
    import dstf.authoring.session as session_mod

    real = session_mod.compare_one

    def failing(test, *args, **kwargs):
        if test.model_id == D and test.comparison_tolerance is None:
            raise ValueError("boom")
        return real(test, *args, **kwargs)

    async with user_simulation(root=shell.index) as user:
        await _open(user)
        monkeypatch.setattr(session_mod, "compare_one", failing)
        shell.session.stage([PatchOp(D, "add", "/comparison", {"tolerance": 0.5})])
        shell.session.stage([LOOSEN])
        _open_drawer(user)
        await user.should_see(marker=f"drawer-test:{A}", retries=20)
        verdicts = sorted(
            user.find(marker="drawer-verdict").elements, key=lambda e: e.id
        )
        assert [e.text for e in verdicts] == [
            "verdict would change scoring failed → PASS",
            "verdict would change FAIL → PASS",
        ]
        assert "a page failed" not in caplog.text


@pytest.fixture
def expression_shell(tmp_path):
    """A's x is a UnitTests variable whose model wrote an expression."""
    from test_session import (
        AWKWARD_EXPRESSION,
        UT1,
        _awkward_expression_project,
        with_expressions,
    )

    cfg, work = _awkward_expression_project(tmp_path)
    s = Shell(cfg, work_dir=work)
    s.session.open(cfg, work_dir=work)
    with_expressions(s.session, A, {UT1: AWKWARD_EXPRESSION})
    s.session._score(A)
    s.load.phase = "done"
    return s


async def test_a_variable_in_the_diff_reads_its_label_and_its_muted_name(
    expression_shell,
):
    from test_session import AWKWARD_EXPRESSION, UT1

    shell = expression_shell
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        _open_drawer(user)
        shell.session.stage([PatchOp(A, "add", "/metrics/children/0/tolerance", 0.5)])
        await user.should_see(marker="diff-muted", retries=20)
        assert {e.text for e in user.find(marker="diff-muted").elements} == {f" {UT1}"}
        assert f'+ "variable": {AWKWARD_EXPRESSION} {UT1},' in _lines(user, "add")


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


async def test_a_discard_clicked_as_a_job_starts_is_a_notification(shell):
    """The header disables Discard on the tick after a job starts; a click
    within that tick reaches a Session that refuses it, and the refusal is
    a notification, not a traceback."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.session.stage([LOOSEN])
        gate = _Gate()
        shell.session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
        try:
            _suite(shell, user).header._discard()  # the click within the tick
            await user.should_see("rerun is still running", retries=20)
            assert len(shell.session.spec.pending) == 2
        finally:
            gate.release.set()


async def test_a_reload_clicked_while_a_job_runs_is_a_notification(shell):
    """The Reload a refused Save offers is refused while a job runs, as
    Discard is — a notification, the dialog left open."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.session.stage([LOOSEN])
        p = shell.session.spec.path
        p.write_text(p.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        header = _suite(shell, user).header
        header._save()
        await user.should_see("Keep my edits", retries=20)
        gate = _Gate()
        shell.session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
        try:
            user.find("Reload").click()
            await user.should_see("rerun is still running", retries=20)
            assert len(shell.session.spec.pending) == 2
        finally:
            gate.release.set()


def _failure_records(caplog) -> list:
    return [r for r in caplog.records if r.exc_info is not None]


async def test_a_discard_that_fails_on_a_bug_is_logged_and_notified(
    shell, monkeypatch, caplog
):
    """Discard's refusal is a person's (a job running): notified, never
    logged. Anything else it raises is a bug: notified AND logged, by the
    rule the action bars use (`log_unless_users`)."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.session.stage([LOOSEN])
        header = _suite(shell, user).header
        gate = _Gate()
        shell.session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
        try:
            header._discard()
            await user.should_see("rerun is still running", retries=20)
            assert _failure_records(caplog) == []
        finally:
            gate.release.set()

        def broken() -> None:
            raise KeyError("a bug's")

        monkeypatch.setattr(shell.session, "discard", broken)
        header._discard()
        await user.should_see("KeyError", retries=20)
        (record,) = _failure_records(caplog)
        assert isinstance(record.exc_info[1], KeyError)


async def test_a_dialog_reload_that_fails_on_a_bug_is_logged_and_notified(
    shell, monkeypatch, caplog
):
    """The Reload a refused Save offers: a JobRunningError is notified only
    (`test_a_reload_clicked_while_a_job_runs_is_a_notification`); a bug is
    notified and logged."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.session.stage([LOOSEN])
        p = shell.session.spec.path
        p.write_text(p.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        header = _suite(shell, user).header
        header._save()
        await user.should_see("Keep my edits", retries=20)
        gate = _Gate()
        shell.session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
        try:
            user.find("Reload").click()
            await user.should_see("rerun is still running", retries=20)
            assert _failure_records(caplog) == []
        finally:
            gate.release.set()
        await _settled(lambda: shell.session.running is None)

        def broken() -> None:
            raise KeyError("a bug's")

        monkeypatch.setattr(shell.session, "reload", broken)
        user.find("Reload").click()
        await user.should_see("KeyError", retries=20)
        (record,) = _failure_records(caplog)
        assert isinstance(record.exc_info[1], KeyError)


def test_shutdown_waits_for_a_rerun_render_still_queued(shell):
    """A Rerun's dashboard render runs on a daemon thread after the job has
    finished: a Stop server or a Ctrl-C right after the rerun must not end
    the process under it — the shutdown waits for it."""
    landed = threading.Event()

    def slow(project, comparisons):
        time.sleep(0.5)
        landed.set()

    jobs: list = []
    shell.session.on(
        "job", lambda e: jobs.append(e["job"]) if e["state"] == "started" else None
    )
    shell.session.apply(
        Job(
            run=lambda ctx: None,
            label="rerun",
            models=frozenset({A}),
            after_reload=slow,
        )
    )
    for _ in range(250):  # the completion is posted to the UI loop's tick
        shell.tick()
        if jobs[0].done.is_set():
            break
        time.sleep(0.02)
    assert jobs[0].done.is_set() and not landed.is_set()
    shell.shutdown()
    assert landed.is_set()


def test_the_app_runs_the_shells_shutdown(tmp_path, monkeypatch):
    import dstf.authoring.shell.app as app_mod

    handlers: list = []
    monkeypatch.setattr(app_mod.app, "on_shutdown", handlers.append)
    monkeypatch.setattr(app_mod.app, "on_startup", lambda fn: None)
    monkeypatch.setattr(app_mod.ui, "run", lambda *a, **k: None)
    monkeypatch.setattr(app_mod.Shell, "mount", lambda self: None)
    cfg, work = _project(tmp_path)
    app_mod.run_app(cfg, work_dir=work, show=False)
    (handler,) = handlers
    assert handler.__func__ is Shell.shutdown


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
        change = _one(user, "change-backend")
        await _settled(lambda: change.enabled)
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
    """Change backend… in the ⋮ menu opens a dialog holding the select
    (the owner's ruling, Q13 A); choosing reopens as the row's select did,
    closes the dialog and renames the backend in the row."""
    cfg, work = _project(tmp_path)
    _with_alt_backend(cfg)
    shell = Shell(cfg, work_dir=work)
    shell.session.open(cfg, work_dir=work)
    shell.load.phase = "done"
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        dialog = _suite(shell, user).header.backend_dialog
        assert not dialog.value
        user.find(marker="change-backend").click()
        await _settled(lambda: dialog.value)
        backend = _one(user, "backend")
        assert dialog in list(_ancestors(backend))
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
        assert not dialog.value
        name = _one(user, "backend-name")
        await _settled(lambda: name.text.startswith("Python (alt) · "))
        # The same family reads the same partition: the verdicts are the old ones.
        assert classify(shell.session.comparisons[A]) == FAIL


async def test_change_backend_is_greyed_with_its_sentence_while_edits_are_pending(
    shell,
):
    """The menu item is disabled with the reason as its second line, as
    Export shows "no pending edits"; the dialog's select is disabled too."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        change = _one(user, "change-backend")
        assert change.enabled and _one(user, "backend").enabled
        header = _suite(shell, user).header
        assert not header.backend_why.visible
        shell.session.stage([LOOSEN])
        await _settled(lambda: not change.enabled)
        assert not _one(user, "backend").enabled
        assert (
            _one(user, "backend:why").text
            == "backend change waits: save or discard 2 pending edits first"
        )
        assert change in list(_ancestors(_one(user, "backend:why")))
        shell.session.discard()
        await _settled(lambda: change.enabled)
        assert _one(user, "backend").enabled and not header.backend_why.visible


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


async def test_the_header_is_one_row_unless_a_page_asks_for_a_second(shell):
    """The Test page's sticky header is a second row INSIDE `ui.header`
    (decision 8); the first row keeps its order, and the Suite page asks for
    no second one."""
    from nicegui import ui

    async with user_simulation(root=shell.index) as user:
        await _open(user)
        row = _one(user, "wordmark").parent_slot.parent
        assert "dstf-header-row" in row.classes
        assert isinstance(row.parent_slot.parent, ui.header)
        marks = [next(iter(c._markers), None) for c in row.default_slot.children]
        order = [
            m
            for m in marks
            if m
            in ("wordmark", "project", "backend-name", "runs", "pending", "header-menu")
        ]
        # Stop server is in the menu at the row's end (3c); the backend is
        # text, its select in the menu's Change backend dialog (Q13 A).
        assert order == [
            "wordmark",
            "project",
            "backend-name",
            "runs",
            "pending",
            "header-menu",
        ]
        assert not [e for e in row.descendants() if isinstance(e, ui.select)]
        with pytest.raises(AssertionError):
            user.find(marker="subheader")


def test_save_caption_counts_new_entries():
    from dstf.authoring.shell.header import save_caption

    loaded = {"tests": [{"model": "M"}]}
    edit = PatchOp("M", "replace", "/plots", [])
    new = PatchOp("N", "add", "/plots", [])
    assert save_caption([], loaded, "test_spec.json") == (
        "Save 0 spec edits (0 tests) to test_spec.json"
    )
    assert save_caption([edit], loaded, "test_spec.json") == (
        "Save 1 spec edit (1 test) to test_spec.json"
    )
    assert save_caption([edit, new], loaded, "test_spec.json") == (
        "Save 2 spec edits (2 tests, 1 new entry) to test_spec.json"
    )
    other = PatchOp("P", "add", "/plots", [])
    assert save_caption([new, other], loaded, "test_spec.json") == (
        "Save 2 spec edits (2 tests, 2 new entries) to test_spec.json"
    )


async def test_save_names_a_new_entry(tmp_path):
    """A layout staged for a test tracked only by its `.mo` annotation
    writes that test's first entry, and Save says so (the owner's ruling,
    round 1 Q4). The Header hands `save_caption` the LOADED document: over
    the effective one the pending op has already made the entry."""
    from dstf.discovery.spec_update import spec_entry
    from test_session import _add_bar, _modelica_project

    cfg = _modelica_project(tmp_path)
    _add_bar(tmp_path)
    shell = Shell(cfg)
    shell.session.open(cfg)
    shell.load.phase = "done"
    bar = "MyLib.Bar"
    assert bar in shell.visible_tests()
    assert spec_entry(shell.session.spec.loaded, bar) is None
    shell.session.stage([PatchOp(bar, "add", "/plots", [{"series": [{"var": "y"}]}])])
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        assert _one(user, "save").text == (
            "Save 1 spec edit (1 test, 1 new entry) to test_spec.json"
        )


def _ancestors(element):
    while element.parent_slot is not None:
        element = element.parent_slot.parent
        yield element


async def test_the_header_menu_holds_the_rarely_used_actions(shell):
    """Export, Change backend…, Open in another backend…, dashboard and Stop
    server are in the ⋮ menu at the row's end (the owner's rulings, Q10 A
    and Q13 A); Save and
    Discard stay in the row. Asked through the element tree, this is what
    sees Export left in the row (the 900 px browser test did not: the
    row's buttons wrapped their text and still fitted)."""
    from nicegui import ui

    async with user_simulation(root=shell.index) as user:
        await _open(user)
        row = _one(user, "wordmark").parent_slot.parent
        for m in ("save", "discard", "header-menu"):
            assert row in list(_ancestors(_one(user, m))), m
        (menu,) = [
            e
            for e in _one(user, "header-menu").default_slot.children
            if isinstance(e, ui.menu)
        ]
        assert not menu.value
        for m in ("export", "change-backend", "other-backend", "dashboard", "stop"):
            assert menu in list(_ancestors(_one(user, m))), m
        # Change backend… sits directly above Open in another backend….
        items = [next(iter(c._markers), None) for c in menu.default_slot.children]
        at = items.index("change-backend")
        assert items[at + 1] == "other-backend", items
        assert not _one(user, "export").enabled
        assert _one(user, "export:why").text == "no pending edits"
        # The ⋮ button says what it holds and that it opens a menu.
        more = _one(user, "header-menu")
        assert more.props["aria-label"] == "More actions"
        assert more.props["aria-haspopup"] == "menu"
        # dashboard is a real link the browser opens in a new tab, never a
        # `window.open` from the server, which a popup blocker may refuse.
        dashboard = _one(user, "dashboard")
        assert dashboard.props["href"] == "/dashboard/dashboard.html"
        assert dashboard.props["target"] == "_blank"
