"""Edit the model, queue it again — against the real simulators, through
`Session.enqueue`, on ONE live worker whose process never changes.

Nothing here is simulated: a scratch copy of the example library is edited
on disk between Queue clicks, and what each run wrote is read back from its
result file. The run after an edit must be the edited model's — its stop
time, its equation, its base class's and its component's text — and a file
that no longer parses must FAIL the test with the simulator's message,
never pass on the model the tool still holds.

Skipped without the tool; deselected in CI by the ``dymola`` / ``ompython``
markers. About half a minute per simulator.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import psutil
import pytest

pytest.importorskip("nicegui")

from dstf.authoring.session import Session
from dstf.backends.common.mat_reader import read_mat_last_time, read_result_mat
from dstf.backends.pool import ItemState
from test_live_results_real import _dymola_available, _ompython_available
from test_session_queue import Loop

EXAMPLE = Path(__file__).parent.parent / "examples" / "modelica" / "ModelicaTestingLib"
M = "ModelicaTestingLib.Examples.SimpleTest"
X = "unitTests.x[1]"

GAIN = """within ModelicaTestingLib.Components;
partial model Gain "a base class of the test's model, in a file of its own"
  parameter Real k = {k};
end Gain;
"""


N = 'parameter Integer n=0 "Number of tracked variables";'
SCALE = "\n  parameter Real scale = {scale};"


def _sub(path: Path, old: str, new: str) -> None:
    """Replace ``old`` by ``new`` in the file, and move its modification
    time on: an edit within one clock tick that keeps the length is no
    change a stamp can see."""
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, (path, old)
    path.write_text(text.replace(old, new), encoding="utf-8")
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def _alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


class Tool:
    """A Session open on a scratch copy of the example library, on a real
    backend, its queue driven from the test's thread."""

    def __init__(self, tmp_path: Path, backend: str):
        self.lib = tmp_path / "ModelicaTestingLib"
        shutil.copytree(EXAMPLE, self.lib)
        # Before the tool opens: the test's model extends a class that has
        # a file of its own, so a later edit of that file changes no list
        # of files.
        (self.lib / "Components" / "Gain.mo").write_text(
            GAIN.format(k=1), encoding="utf-8"
        )
        order = self.lib / "Components" / "package.order"
        order.write_text(order.read_text(encoding="utf-8") + "Gain\n", encoding="utf-8")
        self.model = self.lib / "Examples" / "SimpleTest.mo"
        text = self.model.read_text(encoding="utf-8")
        assert "der(x) = 1;" in text
        self.model.write_text(
            text.replace(
                'Real x(start=0, fixed=true) "Linear ramp";',
                'extends Components.Gain;\n  Real x(start=0, fixed=true) "Linear ramp";',
            ).replace("der(x) = 1;", "der(x) = k*unitTests.scale;"),
            encoding="utf-8",
        )
        # ...and reads a parameter of its component (a result file holds
        # the tracked variables only, so each edit must show in one).
        _sub(self.lib / "Components" / "UnitTests.mo", N, N + SCALE.format(scale=1))
        self.loop = Loop()
        self.session = Session(post=self.loop.post)
        self.session.open(
            self.lib / "Resources" / "ReferenceResults" / "testing.json",
            backend=backend,
            work_dir=tmp_path / "work",
        )
        self.session.rescan_done.wait(60)
        self.loop.pump()
        self.pids: set[int] = set()

    def worker(self):
        (slot,) = self.session._pool._slots.values()
        return slot.runner.worker

    def click(self):
        """One Queue click on the model, waited to its end: the item."""
        (item,) = self.session.enqueue([M])
        self.loop.until(
            lambda: (
                self.session.item_state(item)
                not in (ItemState.WAITING, ItemState.RUNNING)
            ),
            timeout=300,
            what="the run ended",
        )
        self.pids |= set(self.worker().pids)
        return item

    def read(self, item, *names: str) -> tuple[float, dict[str, float]]:
        """The time the run's result file reached, and each name's last
        value in it."""
        work = self.session.project.config.work_dir / item.test_key
        (mat,) = [p for p in work.glob("*.mat") if p.name != "dsin.mat"]
        got = read_result_mat(mat, set(names))
        return read_mat_last_time(mat), {
            name: round(float(got[name][1][-1]), 3) for name in names
        }

    def close(self) -> None:
        self.session.stop_queue()
        self.session.wait_background(30)


def _finished(tool: Tool, *names: str):
    item = tool.click()
    assert item.state is ItemState.FINISHED, item.message
    return tool.read(item, *names)


def _edit_and_queue_again(tool: Tool, broken_says: tuple[str, ...]) -> None:
    lib, model = tool.lib, tool.model
    assert _finished(tool, X) == (10.0, {X: 10.0})
    (pid,) = tool.worker().pids
    born = psutil.Process(pid).create_time()

    # Its experiment annotation: read by the tool, not by the simulator.
    _sub(model, "StopTime=10", "StopTime=4")
    assert _finished(tool, X) == (4.0, {X: 4.0})
    assert tool.session.project.test(M).stop_time == 4

    # An equation of the model.
    _sub(model, "der(x) = k*", "der(x) = 3*k*")
    assert _finished(tool, X) == (4.0, {X: 12.0})

    # The base class's file.
    _sub(lib / "Components" / "Gain.mo", "Real k = 1;", "Real k = 2;")
    assert _finished(tool, X) == (4.0, {X: 24.0})

    # A component's file.
    _sub(
        lib / "Components" / "UnitTests.mo",
        SCALE.format(scale=1),
        SCALE.format(scale=2),
    )
    assert _finished(tool, X) == (4.0, {X: 48.0})

    # Broken: the tool still HOLDS a model that runs. It must not run it.
    _sub(model, "der(x) = 3*k*", "der(x) = 3*k +*")
    item = tool.click()
    assert item.state is ItemState.FAILED, "a broken model PASSED on the old one"
    assert item.message and all(part in item.message for part in broken_says)
    again = tool.click()  # and asked again, it fails again
    assert again.state is ItemState.FAILED and again.message == item.message

    # Fixed.
    _sub(model, "der(x) = 3*k +*", "der(x) = 5*k*")
    assert _finished(tool, X) == (4.0, {X: 80.0})

    # Nothing changed: the same state, nothing read, the same result.
    before = tool.session._library
    assert _finished(tool, X) == (4.0, {X: 80.0})
    assert tool.session._library is before

    # ONE simulator process did all of it.
    assert tool.pids == {pid} and tool.worker().pids == {pid}
    assert psutil.Process(pid).create_time() == born


@pytest.mark.ompython
@pytest.mark.skipif(not _ompython_available(), reason="needs omc + OMPython")
@pytest.mark.parametrize("how", ["whole library", "file by file"])
def test_openmodelica_runs_the_model_as_it_is_on_disk(tmp_path, monkeypatch, how):
    from dstf.backends.openmodelica.persistent_backend import OpenModelicaWorker

    asked: list[str] = []
    real = OpenModelicaWorker._load_whole

    def whole(self, package_file, name):
        asked.append(name)
        return real(self, package_file, name)

    monkeypatch.setattr(OpenModelicaWorker, "_load_whole", whole)
    if how == "file by file":
        # This library's whole load is a tenth of a second, so the worker
        # would always choose it: make one file cost nothing instead.
        monkeypatch.setattr(OpenModelicaWorker, "_FILE_LOAD_SECONDS", 0.0)
    tool = Tool(tmp_path, "OpenModelica")
    try:
        _edit_and_queue_again(
            tool,
            ("ModelicaTestingLib does not load", "[syntax:error:"),
        )
        # Six clicks found a changed file (the broken one twice over).
        assert len(asked) == (0 if how == "file by file" else 7)

        # A class renamed inside its file — the base class's: the whole
        # load refuses the file, and so does the load of that one file.
        # The class omc holds must not stay and run. (The test's OWN file
        # never gets this far: the Session refuses a test its file no
        # longer holds.)
        gain = tool.lib / "Components" / "Gain.mo"
        _sub(gain, "partial model Gain ", "partial model GainRenamed ")
        _sub(gain, "end Gain;", "end GainRenamed;")
        item = tool.click()
        assert item.state is ItemState.FAILED, "ran on the class omc still held"
        assert "ModelicaTestingLib does not load" in item.message
        assert "GainRenamed" in item.message
    finally:
        pids = set(tool.pids)
        tool.close()
    assert not [p for p in pids if _alive(p)]


@pytest.mark.dymola
@pytest.mark.skipif(not _dymola_available(), reason="needs Dymola")
def test_dymola_runs_the_model_as_it_is_on_disk(tmp_path):
    tool = Tool(tmp_path, "Dymola")
    try:
        # Dymola opens a library whose file does not parse without a word;
        # the test's translation is what fails, with its generic message.
        _edit_and_queue_again(tool, ("ranslat",))
    finally:
        pids = set(tool.pids)
        tool.close()
    assert not [p for p in pids if _alive(p)]
