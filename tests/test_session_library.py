"""A Queue click runs the library AS IT IS ON DISK (`Session.enqueue`): one
sweep of the source files per click, the queued tests whose own file
changed read again into the project's scan, and the swept state on every
item of the click — so a worker that stays alive reads the library again
before it runs one.

A small Modelica library on disk; the queue's backend is the fake
persistent one of `test_library_reload` (its worker logs each reload and
each run), in place of a simulator."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("nicegui")

from dstf.authoring import session as session_module
from dstf.authoring.capabilities import Enqueue
from dstf.authoring.queue_rows import (
    CHECKING,
    READING,
    counts_text,
    indicator,
    queue_row,
    queue_rows,
    starting_text,
    starting_texts,
    stop_reason,
)
from dstf.authoring.session import QueueError, Session
from dstf.authoring.situation import strip_for
from dstf.authoring.spec_document import PatchOp
from dstf.authoring.suite_rows import suite_row
from dstf.backends.pool import ItemState, StartingWorker
from dstf.discovery.library_state import LibraryChanges
from dstf.discovery.scan_cache import load_scan
from dstf.reporting.status import QUEUED
from test_library_reload import _ReloadingBackend
from test_session_queue import Loop, fake_item

A, B, C, SPEC = "Lib.A", "Lib.B", "Lib.C", "Lib.Parts.Spec"

PACKAGE = "within ;\npackage Lib\nend Lib;\n"


def _model(name: str, stop_time: float = 10) -> str:
    return f"""\
within Lib;
model {name}
  Real x(start=0, fixed=true);
  Components.UnitTests unitTests(n=1, x={{x}});
equation
  der(x) = 1;
  annotation (experiment(StopTime={stop_time:g}, Tolerance=1e-6));
end {name};
"""


def _parts(stop_time: float = 20) -> str:
    """A package file holding a model only `test_spec.json` declares."""
    return f"""\
within Lib;
package Parts
  model Spec
    Real y;
  equation
    y = time;
    annotation (experiment(StopTime={stop_time:g}));
  end Spec;
end Parts;
"""


def _touch_later(path: Path) -> None:
    """A rewrite within one clock tick, of the same length, is no change a
    stamp can see: move the modification time on, as a save a moment later
    would."""
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    _touch_later(path)


class Lib:
    """The library, its project files, and a Session open on it whose
    queue runs on the fake backend."""

    def __init__(self, tmp_path: Path, monkeypatch, *, loop: bool = False):
        self.dir = tmp_path / "Lib"
        self.dir.mkdir()
        (self.dir / "package.mo").write_text(PACKAGE, encoding="utf-8")
        for name in ("A", "B", "C"):
            (self.dir / f"{name}.mo").write_text(_model(name), encoding="utf-8")
        (self.dir / "Parts.mo").write_text(_parts(), encoding="utf-8")
        refs = tmp_path / "refs"
        refs.mkdir()
        self.cfg = refs / "testing.json"
        self.cfg.write_text(
            json.dumps(
                {
                    "source_path": "../Lib",
                    "backend": "Python",
                    "backends": {"Python": ["python3", "python"]},
                    "test_spec": "test_spec.json",
                    "reference_root": ".",
                }
            ),
            encoding="utf-8",
        )
        (refs / "test_spec.json").write_text(
            json.dumps({"tests": [{"model": SPEC, "variables": ["y"]}]}),
            encoding="utf-8",
        )
        self.backends: list[_ReloadingBackend] = []
        real = session_module.select_backend

        def select(config, persistent=False):
            if not persistent:
                return real(config, persistent=False)
            self.backends.append(_ReloadingBackend(config))
            return self.backends[-1]

        monkeypatch.setattr(session_module, "select_backend", select)
        self.loop = Loop()
        self.session = Session(post=self.loop.post if loop else None)
        self.events: list[dict] = []
        for event in ("spec", "queue", "notice"):
            self.session.on(event, self.events.append)
        self.session.open(self.cfg, work_dir=tmp_path / "work")
        self.session.rescan_done.wait(30)
        self.loop.pump()
        self.events.clear()

    @property
    def backend(self) -> _ReloadingBackend:
        return self.backends[-1]

    def test(self, model_id: str):
        return self.session.project.test(model_id)

    def ended(self, *items) -> None:
        self.loop.until(
            lambda: all(
                self.session.item_state(i) not in (ItemState.WAITING, ItemState.RUNNING)
                for i in items
            ),
            what="ended",
        )

    def spec_events(self) -> list[set[str]]:
        return [set(e["models"]) for e in self.events if e["event"] == "spec"]

    def notices(self) -> list[str]:
        return [e["message"] for e in self.events if e["event"] == "notice"]


@pytest.fixture
def lib(tmp_path, monkeypatch):
    made = Lib(tmp_path, monkeypatch)
    yield made
    _end(made)


@pytest.fixture
def looped(tmp_path, monkeypatch):
    made = Lib(tmp_path, monkeypatch, loop=True)
    yield made
    _end(made)


def _end(made: Lib) -> None:
    for backend in made.backends:
        for gate in backend.gates.values():
            gate.set()
        if backend.reload_gate is not None:
            backend.reload_gate.set()
    made.session.stop_queue()
    made.session.wait_background(30)
    assert not [t for t in threading.enumerate() if "-pool-" in t.name]


def _kinds(log) -> list[str]:
    return [entry[0] for entry in log]


# ---------------------------------------------------------------------------
# One sweep per click, and the state its items carry
# ---------------------------------------------------------------------------


def test_the_project_is_as_the_tests_below_take_it(lib):
    assert {t.model_id: t.source for t in lib.session.project.tests} == {
        A: "unit_tests",
        B: "unit_tests",
        C: "unit_tests",
        SPEC: "spec",
    }
    assert (lib.test(A).stop_time, lib.test(SPEC).stop_time) == (10, 20)


def test_the_items_of_one_click_carry_one_state_and_the_next_click_the_same(lib):
    a, b = lib.session.enqueue([A, B])
    lib.ended(a, b)
    assert a.library is b.library and a.library.serial == 1
    assert set(a.library.stamps) == set(lib.dir.rglob("*.mo"))
    (c,) = lib.session.enqueue([C])
    lib.ended(c)
    # Nothing changed on disk: the SAME object, and the worker read nothing.
    assert c.library is a.library
    assert _kinds(lib.backend.log) == ["run", "run", "run"]


def test_a_changed_file_is_the_next_state_and_the_live_worker_reads_it(lib):
    (a,) = lib.session.enqueue([A])
    lib.ended(a)
    _write(lib.dir / "B.mo", _model("B") + "// edited\n")
    b, c = lib.session.enqueue([B, C])
    lib.ended(b, c)
    assert b.library is c.library and b.library.serial == 2
    assert _kinds(lib.backend.log) == ["run", "reload", "run", "run"]
    assert lib.backend.log[1][1] == LibraryChanges(changed=(lib.dir / "B.mo",))
    assert lib.backend.starts == [0]  # one worker, alive throughout


def test_a_source_no_worker_keeps_loaded_is_not_swept(tmp_path):
    from test_session_queue import Harness, _short

    h = Harness(tmp_path)  # a Python project: one process per test
    try:
        (item,) = h.session.enqueue(["FakeLib.A"])
        assert item.library is None and h.session._library is None
        h.ended("FakeLib.A")
        assert _short(item.model_id) == "A"
    finally:
        h.session.stop_queue()
        h.session.wait_background(30)


# ---------------------------------------------------------------------------
# The queued tests are read from their files again
# ---------------------------------------------------------------------------


def test_a_changed_stop_time_is_read_into_the_project_before_it_is_queued(lib):
    (first,) = lib.session.enqueue([A])
    lib.ended(first)
    lib.events.clear()
    _write(lib.dir / "A.mo", _model("A", stop_time=4))

    (item,) = lib.session.enqueue([A])

    assert item.test.stop_time == 4
    # Not in the item alone: the project's test — the merged one the item
    # runs — its scan and the cache.
    assert lib.test(A).stop_time == 4 and item.test is lib.test(A)
    assert lib.session.project.scanned[A].stop_time == 4
    lib.session.wait_background(30)
    assert load_scan(lib.session.project.config)[A].stop_time == 4
    # What tells a page a test's definition changed: the `spec` event.
    assert lib.spec_events() == [{A}]
    lib.ended(item)
    assert lib.backend.log[-1] == ("run", A, 4)


def test_only_the_queued_tests_files_that_changed_are_read_again(lib, monkeypatch):
    read: list[list[str]] = []
    real = session_module.scan_files

    def spy(config, files, progress=None):
        read.append(sorted(Path(f).name for f in files))
        return real(config, files, progress)

    monkeypatch.setattr(session_module, "scan_files", spy)
    a, b = lib.session.enqueue([A, B])
    lib.ended(a, b)
    # Read by the scan at open, at a moment nobody stamped: once now.
    assert read == [["A.mo", "B.mo"]]
    _write(lib.dir / "A.mo", _model("A", stop_time=4))
    _write(lib.dir / "C.mo", _model("C", stop_time=5))  # not queued: not read
    a, b = lib.session.enqueue([A, B])
    lib.ended(a, b)
    assert read == [["A.mo", "B.mo"], ["A.mo"]]
    assert lib.test(C).stop_time == 10


def test_a_test_whose_file_is_gone_is_refused_and_the_others_are_queued(lib):
    (lib.dir / "B.mo").unlink()
    (a,) = lib.session.enqueue([A, B])
    said = f"{B}: its file {lib.dir / 'B.mo'} is gone"
    assert a.model_id == A
    assert lib.notices() == [said] and lib.session.notices == [said]
    lib.ended(a)
    with pytest.raises(QueueError) as raised:
        lib.session.enqueue([B])
    assert str(raised.value) == said


def test_a_test_whose_file_no_longer_holds_it_is_refused(lib):
    _write(lib.dir / "B.mo", _model("Renamed"))
    with pytest.raises(QueueError) as raised:
        lib.session.enqueue([B])
    assert str(raised.value) == f"{B}: its file {lib.dir / 'B.mo'} no longer holds it"
    assert lib.session.queue == []


def test_the_note_of_a_refused_test_goes_when_it_is_next_queued(lib):
    """`<model>: its file … is gone` stood in the panel after the file was
    back and the test had run and passed again."""
    text = (lib.dir / "B.mo").read_text(encoding="utf-8")
    (lib.dir / "B.mo").unlink()
    (a,) = lib.session.enqueue([A, B])
    said = f"{B}: its file {lib.dir / 'B.mo'} is gone"
    assert lib.session.notices == [said]
    lib.ended(a)
    # Queued again with the file still gone: said once more, each time.
    (c,) = lib.session.enqueue([C, B])
    assert lib.session.notices == [said, said]
    lib.ended(c)
    lib.events.clear()

    (lib.dir / "B.mo").write_text(text, encoding="utf-8")
    (b,) = lib.session.enqueue([B])
    assert lib.session.notices == []
    # ...and the panel is told: a `notice` event with no message.
    assert [e for e in lib.events if e["event"] == "notice"] == [{"event": "notice"}]
    lib.ended(b)
    # Another test's note is not this test's to take.
    (lib.dir / "C.mo").unlink()
    with pytest.raises(QueueError):
        lib.session.enqueue([C])
    (again,) = lib.session.enqueue([B])
    lib.ended(again)
    assert lib.session._refusals == {C: f"{C}: its file {lib.dir / 'C.mo'} is gone"}


def test_with_a_loop_a_note_that_says_two_refusals_loses_the_one_that_is_over(looped):
    lib = looped
    texts = {m: (lib.dir / f"{m[-1]}.mo").read_text(encoding="utf-8") for m in (B, C)}
    for name in ("B", "C"):
        (lib.dir / f"{name}.mo").unlink()
    lib.session.apply(Enqueue(models=(B, C)))  # all refused: one notice
    gone = {m: f"{m}: its file {lib.dir / (m[-1] + '.mo')} is gone" for m in (B, C)}
    lib.loop.until(lambda: lib.notices() != [], what="the notice")
    assert lib.session.notices == [f"{gone[B]}; {gone[C]}"]
    (lib.dir / "B.mo").write_text(texts[B], encoding="utf-8")
    lib.session.apply(Enqueue(models=(B,)))
    lib.loop.until(lambda: len(lib.session.queue) == 1, what="queued")
    assert lib.session.notices == [gone[C]]
    lib.ended(*lib.session.queue)


@pytest.mark.skipif(
    os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="a file mode that denies its owner the read",
)
@pytest.mark.parametrize("model", [B, SPEC], ids=["scanned", "spec only"])
def test_a_file_that_cannot_be_read_is_refused_in_the_systems_words(lib, model):
    """Not "no longer holds it": the file was not edited, it cannot be
    opened."""
    path = lib.dir / ("B.mo" if model == B else "Parts.mo")
    _touch_later(path)
    path.chmod(0)
    try:
        with pytest.raises(QueueError) as refused:
            lib.session.enqueue([model])
    finally:
        path.chmod(0o644)
    assert str(refused.value) == (
        f"{model}: its file {path} cannot be read: Permission denied"
    )
    (item,) = lib.session.enqueue([model])  # readable again: queued
    lib.ended(item)
    assert lib.session.notices == []


def test_a_spec_only_tests_model_is_found_again_when_its_file_changed(lib):
    (first,) = lib.session.enqueue([SPEC])
    lib.ended(first)
    assert first.test.stop_time == 20
    lib.events.clear()
    _write(lib.dir / "Parts.mo", _parts(stop_time=6))

    (item,) = lib.session.enqueue([SPEC])

    assert item.test.stop_time == 6 and lib.test(SPEC).stop_time == 6
    assert lib.test(SPEC).field_sources["stop_time"] == "annotation"
    assert lib.spec_events() == [{SPEC}]
    lib.ended(item)
    assert lib.backend.log[-1] == ("run", SPEC, 6)
    # And unchanged since: nothing is looked for again.
    lib.events.clear()
    (again,) = lib.session.enqueue([SPEC])
    lib.ended(again)
    assert lib.spec_events() == []


def test_a_spec_only_test_whose_model_is_gone_is_refused(lib):
    _write(lib.dir / "Parts.mo", "within Lib;\npackage Parts\nend Parts;\n")
    with pytest.raises(QueueError) as raised:
        lib.session.enqueue([SPEC])
    assert str(raised.value) == (
        f"{SPEC}: its file {lib.dir / 'Parts.mo'} no longer holds it"
    )
    (lib.dir / "Parts.mo").unlink()
    with pytest.raises(QueueError) as raised:
        lib.session.enqueue([SPEC])
    assert str(raised.value) == f"{SPEC}: its file {lib.dir / 'Parts.mo'} is gone"


def test_a_spec_edit_hands_a_waiting_item_the_test_as_the_click_read_it(lib):
    """What the click read is the PROJECT's: a re-merge after it (`stage`,
    `_update_waiting`) must not hand the waiting item the test read at
    open again."""
    (first,) = lib.session.enqueue([A])
    lib.ended(first)
    lib.backend.gate(A)
    (a,) = lib.session.enqueue([A])
    lib.loop.until(lambda: a.state is ItemState.RUNNING, what="A running")
    _write(lib.dir / "C.mo", _model("C", stop_time=4))
    (c,) = lib.session.enqueue([C])
    assert c.state is ItemState.WAITING and c.test.stop_time == 4

    lib.session.stage([PatchOp(C, "add", "/comparison/tolerance", 0.5)])

    assert c.test.comparison_tolerance == 0.5  # the edit reached it
    assert c.test.stop_time == 4  # and the click's read is still what it runs
    assert c.library.serial == 2  # its state is the click's, as it was
    lib.backend.gates[A].set()
    lib.ended(a, c)
    assert lib.backend.log[-1] == ("run", C, 4)


def test_a_click_on_a_test_that_waits_hands_it_what_this_click_read(lib):
    (first,) = lib.session.enqueue([A])
    lib.ended(first)
    lib.backend.gate(A)
    (a,) = lib.session.enqueue([A])
    lib.loop.until(lambda: a.state is ItemState.RUNNING, what="A running")
    (c,) = lib.session.enqueue([C])
    assert (c.test.stop_time, c.library.serial) == (10, 1)
    _write(lib.dir / "C.mo", _model("C", stop_time=4))

    (b,) = lib.session.enqueue([C, B])  # C is already queued: skipped

    assert b.model_id == B and [i.model_id for i in lib.session.queue][-2:] == [C, B]
    # ...and runs what this click read, on a worker that has read it since.
    assert c.test.stop_time == 4 and c.library is b.library
    assert c.library.serial == 2
    lib.backend.gates[A].set()
    lib.ended(a, c, b)
    assert [e[:2] for e in lib.backend.log[-3:]] == [
        ("reload", LibraryChanges(changed=(lib.dir / "C.mo",))),
        ("run", C),
        ("run", B),
    ]


def test_the_rescan_does_not_take_back_what_a_click_read(lib):
    """A cached open's rescan lands after a click: it read every file at a
    moment nobody stamped — for all anyone knows, before the edit the click
    read — so the click's tests stay as the click read them."""
    session = lib.session
    project = session.project
    cached = dict(project.scanned)
    _write(lib.dir / "A.mo", _model("A", stop_time=4))
    (a,) = session.enqueue([A])
    lib.ended(a)
    assert lib.test(A).stop_time == 4
    stale = dict(cached)  # what a rescan that read A before the edit found
    stale[C] = session_module.scan_files(project.config, [lib.dir / "C.mo"])[C]
    stale[C].stop_time = 7  # ...and something it is right about

    session._take_rescan(project, stale, {C})

    assert lib.test(A).stop_time == 4 and project.scanned[A].stop_time == 4
    assert lib.test(C).stop_time == 7


# ---------------------------------------------------------------------------
# Off the UI loop
# ---------------------------------------------------------------------------


def test_with_a_loop_the_click_returns_before_the_library_is_read(looped, monkeypatch):
    lib = looped
    sweeping, go = threading.Event(), threading.Event()
    on: list[str] = []
    real = session_module.source_stamps

    def slow(config):
        on.append(threading.current_thread().name)
        sweeping.set()
        assert go.wait(30)
        return real(config)

    monkeypatch.setattr(session_module, "source_stamps", slow)
    queued_on: list[str] = []
    lib.session.on(
        "queue",
        lambda e: (
            e["state"] == "queued" and queued_on.append(threading.current_thread().name)
        ),
    )

    lib.session.apply(Enqueue(models=(A, B)))  # returns with the sweep held

    assert sweeping.wait(30) and on == ["dstf-background"]
    assert lib.session.queue == []
    # A second click on a test whose click is still being read is refused...
    with pytest.raises(QueueError, match=f"{A} is already being queued"):
        lib.session.apply(Enqueue(models=(A,)))
    # ...and one that names another test too takes only that one.
    lib.session.apply(Enqueue(models=(A, C)))
    go.set()
    lib.loop.until(lambda: len(lib.session.queue) == 3, what="queued")
    # Queued on the loop — the thread that pumps it — never on the reader's.
    assert [i.model_id for i in lib.session.queue] == [A, B, C]
    assert queued_on == [threading.current_thread().name] * 2
    assert "dstf-background" in lib.loop.threads  # what handed it to the loop
    lib.ended(*lib.session.queue)
    assert on == ["dstf-background", "dstf-background"]


def _held_sweep(monkeypatch):
    """The sweep of a click's read, held until ``go`` is set."""
    sweeping, go = threading.Event(), threading.Event()
    real = session_module.source_stamps

    def slow(config):
        sweeping.set()
        assert go.wait(30)
        return real(config)

    monkeypatch.setattr(session_module, "source_stamps", slow)
    return sweeping, go


def _said(rows) -> list[tuple]:
    return [(r.number, r.model_id, r.words, r.status, r.actions) for r in rows]


def test_with_a_loop_a_clicks_tests_show_as_taken_before_the_read_is_back(
    looped, monkeypatch
):
    """Between the click and the read nothing on the page moved — a tenth
    of a second on a small library, seconds on a large one on a slow mount
    — and a click that seems to go nowhere is clicked again."""
    lib = looped
    sweeping, go = _held_sweep(monkeypatch)
    lib.session.apply(Enqueue(models=(B, A)))
    assert sweeping.wait(30)

    # No item yet, and every surface says the tests were taken: in project
    # order, the order they will be queued in.
    assert lib.session.queue == [] and lib.session.checking == (A, B)
    words = f"waiting · {CHECKING}"
    assert words == "waiting · checking the library"
    rows = queue_rows(lib.session)
    assert _said(rows) == [(1, A, words, QUEUED, ()), (2, B, words, QUEUED, ())]
    assert [(r.state, r.ahead) for r in rows] == [
        (ItemState.WAITING, 0),
        (ItemState.WAITING, 1),
    ]
    assert indicator(lib.session).text == "2 queued"
    assert counts_text(rows) == "2 queued"
    assert stop_reason(lib.session) == "2 tests are queued or running"
    assert suite_row(lib.session, A).status == QUEUED
    assert suite_row(lib.session, B).status == QUEUED
    assert suite_row(lib.session, C).status != QUEUED
    assert strip_for(queue=queue_row(lib.session, A)).sentence == f"Queued — {words}"
    # Said by an event that names them: their rows are repainted.
    assert [(e["state"], e["models"]) for e in lib.events if e["event"] == "queue"] == [
        ("checking", {A, B})
    ]
    # A second click on one of them is still refused, in its own words.
    with pytest.raises(QueueError, match=f"{A} is already being queued"):
        lib.session.apply(Enqueue(models=(A,)))

    go.set()
    lib.loop.until(lambda: len(lib.session.queue) == 2, what="queued")
    assert lib.session.checking == ()
    rows = queue_rows(lib.session)
    assert [r.model_id for r in rows] == [B, A] and CHECKING not in rows[1].words
    lib.ended(*lib.session.queue)


def test_a_test_that_already_waits_has_its_one_row_while_a_click_on_it_is_read(
    looped, monkeypatch
):
    lib = looped
    lib.session.apply(Enqueue(models=(C,)))  # the pool, and its backend
    lib.loop.until(lambda: len(lib.session.queue) == 1, what="queued")
    lib.ended(*lib.session.queue)
    lib.backend.gate(A)
    lib.session.apply(Enqueue(models=(A, B)))
    lib.loop.until(lambda: len(lib.session.queue) == 3, what="queued")
    sweeping, go = _held_sweep(monkeypatch)
    lib.session.apply(Enqueue(models=(B, C)))  # B waits already
    assert sweeping.wait(30)
    assert lib.session.checking == (C,)
    assert [r.model_id for r in queue_rows(lib.session)] == [C, A, B, C]
    go.set()
    lib.loop.until(lambda: len(lib.session.queue) == 4, what="queued")
    lib.backend.gates[A].set()
    lib.ended(*lib.session.queue)


def test_a_test_the_read_refuses_loses_its_row_and_says_why(looped, monkeypatch):
    lib = looped
    (lib.dir / "B.mo").unlink()
    sweeping, go = _held_sweep(monkeypatch)
    lib.session.apply(Enqueue(models=(A, B)))
    assert sweeping.wait(30)
    assert [r.model_id for r in queue_rows(lib.session)] == [A, B]
    go.set()
    said = f"{B}: its file {lib.dir / 'B.mo'} is gone"
    lib.loop.until(lambda: lib.notices() == [said], what="the notice")
    assert lib.session.checking == ()
    assert [r.model_id for r in queue_rows(lib.session)] == [A]
    assert suite_row(lib.session, B).status != QUEUED
    # The event that takes B's row back names it.
    states = [(e["state"], e["models"]) for e in lib.events if e["event"] == "queue"]
    assert ("refused", {B}) in states and ("queued", {A}) in states
    lib.ended(*lib.session.queue)


def test_a_refused_click_of_one_test_takes_its_row_back(looped, monkeypatch):
    lib = looped
    (lib.dir / "B.mo").unlink()
    sweeping, go = _held_sweep(monkeypatch)
    lib.session.apply(Enqueue(models=(B,)))
    assert sweeping.wait(30) and lib.session.checking == (B,)
    go.set()
    lib.loop.until(lambda: lib.notices() != [], what="the notice")
    assert lib.session.checking == () and queue_rows(lib.session) == []
    assert [(e["state"], e["models"]) for e in lib.events if e["event"] == "queue"] == [
        ("checking", {B}),
        ("refused", {B}),
    ]


def test_with_a_loop_a_refusal_after_the_read_is_a_notice(looped):
    lib = looped
    (lib.dir / "B.mo").unlink()
    lib.session.apply(Enqueue(models=(B,)))
    said = f"{B}: its file {lib.dir / 'B.mo'} is gone"
    lib.loop.until(lambda: lib.notices() == [said], what="the notice")
    assert lib.session.queue == [] and lib.session._reading == set()


def test_with_a_loop_a_refusal_known_before_the_read_is_raised_to_the_click(looped):
    lib = looped
    lib.session.queue_refusal = "Stub runs whole batches only"
    with pytest.raises(QueueError, match="whole batches only"):
        lib.session.apply(Enqueue(models=(A,)))
    with pytest.raises(KeyError):
        lib.session.apply(Enqueue(models=("Lib.Unknown",)))
    assert lib.session._reading == set()
    lib.session.apply(Enqueue(models=()))  # nothing asked: nothing read
    assert lib.session.wait_background(30)
    assert "dstf-background" not in lib.loop.threads


def test_a_click_still_being_read_when_the_queue_stops_queues_nothing(
    looped, monkeypatch
):
    lib = looped
    sweeping, go = threading.Event(), threading.Event()
    real = session_module.source_stamps

    def slow(config):
        sweeping.set()
        assert go.wait(30)
        return real(config)

    monkeypatch.setattr(session_module, "source_stamps", slow)
    lib.session.apply(Enqueue(models=(A,)))
    assert sweeping.wait(30)
    lib.events.clear()
    lib.session.stop_queue()  # the tool stopping
    # The row that said A was taken goes with the stop, by name.
    assert lib.session.checking == ()
    assert [(e["state"], e["models"]) for e in lib.events] == [("stopped", {A})]
    go.set()
    assert lib.session.wait_background(30)
    lib.loop.pump()
    assert lib.session.queue == [] and lib.session._pool is None
    assert [e["state"] for e in lib.events] == ["stopped"]  # and nothing after
    assert not [t for t in threading.enumerate() if "-pool-" in t.name]
    # The stop forgot the click: the test can be queued again.
    monkeypatch.setattr(session_module, "source_stamps", real)
    lib.session.apply(Enqueue(models=(A,)))
    lib.loop.until(lambda: len(lib.session.queue) == 1, what="queued again")
    lib.ended(*lib.session.queue)


def test_a_project_opened_again_starts_from_no_state_of_the_library(lib):
    """The swept state and the stamps the tests were read at are the
    project's: a reopen reads its tests anew, at a moment nobody stamped."""
    (item,) = lib.session.enqueue([A])
    lib.ended(item)
    _write(lib.dir / "B.mo", _model("B", stop_time=4))
    (again,) = lib.session.enqueue([B])
    lib.ended(again)
    assert lib.session._library.serial == 2 and B in lib.session._read_at
    lib.session.stop_queue()
    lib.session.open(lib.cfg, work_dir=lib.cfg.parent.parent / "work")
    lib.session.rescan_done.wait(30)
    assert lib.session._library is None and lib.session._read_at == {}
    (first,) = lib.session.enqueue([A])
    assert first.library.serial == 1
    lib.ended(first)


# ---------------------------------------------------------------------------
# The words while a worker reads
# ---------------------------------------------------------------------------


def test_a_reading_worker_is_said_in_one_phrase_on_the_row_the_strip_and_its_line(
    lib,
):
    assert READING == "reading the library"
    session = lib.session
    item = fake_item(session, A, ItemState.RUNNING)
    reading = StartingWorker(
        0, 'loadFile("/a/long/path/Lib/package.mo")', 12.4, reload=True
    )
    session._pool = SimpleNamespace(
        starting=(reading,), can_cancel=True, stopped=None, close=lambda: None
    )
    try:
        row = queue_row(session, A)
        assert row.words == "running · reading the library"
        assert row.progress == READING and row.worker == 1
        assert strip_for(queue=row).sentence == (
            "Running on worker 1 — reading the library"
        )
        assert starting_texts(session) == [
            'worker 1 reading the library — loadFile("…/package.mo") · 12 s'
        ]
        assert starting_text(StartingWorker(1, "", 3.0, reload=True)) == (
            "worker 2 reading the library · 3 s"
        )
    finally:
        session._live.pop(A, None)
        session.queue.remove(item)
        session._pool = None
