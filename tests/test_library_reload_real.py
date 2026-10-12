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

import json
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


class _Live:
    """A Session open on a scratch project, on a real backend, its queue
    driven from the test's thread."""

    model: str

    def _open(self, config: Path, backend: str, work: Path) -> None:
        self.loop = Loop()
        self.session = Session(post=self.loop.post)
        self.session.open(config, backend=backend, work_dir=work)
        self.session.rescan_done.wait(60)
        self.loop.pump()
        self.pids: set[int] = set()

    def worker(self):
        (slot,) = self.session._pool._slots.values()
        return slot.runner.worker

    def click(self):
        """One Queue click on the model, waited to its end: the item."""
        (item,) = self.session.enqueue([self.model])
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


class Tool(_Live):
    """The example library, a scratch copy."""

    model = M

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
        self.file = self.lib / "Examples" / "SimpleTest.mo"
        text = self.file.read_text(encoding="utf-8")
        assert "der(x) = 1;" in text
        self.file.write_text(
            text.replace(
                'Real x(start=0, fixed=true) "Linear ramp";',
                'extends Components.Gain;\n  Real x(start=0, fixed=true) "Linear ramp";',
            ).replace("der(x) = 1;", "der(x) = k*unitTests.scale;"),
            encoding="utf-8",
        )
        # ...and reads a parameter of its component (a result file holds
        # the tracked variables only, so each edit must show in one).
        _sub(self.lib / "Components" / "UnitTests.mo", N, N + SCALE.format(scale=1))
        self._open(
            self.lib / "Resources" / "ReferenceResults" / "testing.json",
            backend,
            tmp_path / "work",
        )


def _finished(tool: Tool, *names: str):
    item = tool.click()
    assert item.state is ItemState.FINISHED, item.message
    return tool.read(item, *names)


def _edit_and_queue_again(tool: Tool, broken_says: tuple[str, ...]) -> None:
    lib, model = tool.lib, tool.file
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


# ---------------------------------------------------------------------------
# What a simulator loads from more than one place
# ---------------------------------------------------------------------------

T1 = "PartsLib.Tests.T1"

#: ``x(1)`` of the test's model spells which text of each part ran: the
#: ones are `Parts.K`, the tens `Parts.Sub.Deep` (a sub-package with a
#: directory of its own), the hundreds `Parts.Enc`, the thousands the
#: dependency's `DepLib.C`.
_PARTS = {
    "package.mo": "package PartsLib\nend PartsLib;\n",
    "package.order": "Parts\nTests\n",
    "Parts/package.mo": "within PartsLib;\npackage Parts\nend Parts;\n",
    "Parts/package.order": "K\nEnc\nSub\n",
    "Parts/K.mo": ("within PartsLib.Parts;\npackage K\n  constant Real k=1;\nend K;\n"),
    "Parts/Enc.mo": (
        "within PartsLib.Parts;\nmodel Enc\n  parameter Real e=1;\n"
        "  Real v=e;\nend Enc;\n"
    ),
    "Parts/Sub/package.mo": "within PartsLib.Parts;\npackage Sub\nend Sub;\n",
    "Parts/Sub/package.order": "Deep\n",
    "Parts/Sub/Deep.mo": (
        "within PartsLib.Parts.Sub;\npackage Deep\n  constant Real d=1;\nend Deep;\n"
    ),
    "Tests/package.mo": "within PartsLib;\npackage Tests\nend Tests;\n",
    "Tests/package.order": "T1\n",
    "Tests/T1.mo": (
        "within PartsLib.Tests;\nmodel T1\n  Real x(start=0, fixed=true);\n"
        "  Parts.Enc enc;\nequation\n"
        "  der(x) = Parts.K.k + 10*Parts.Sub.Deep.d + 100*enc.v + 1000*DepLib.C.c;\n"
        "  annotation(experiment(StopTime=1, Tolerance=1e-6));\nend T1;\n"
    ),
}
_DEP = {
    "package.mo": "package DepLib\nend DepLib;\n",
    "package.order": "C\n",
    "C.mo": "within DepLib;\npackage C\n  constant Real c=1;\nend C;\n",
}


class Parts(_Live):
    """A generated library with a second one as a path dependency.
    ``arrange(parts)`` runs on the files before the tool opens and returns
    the dependency's entry for ``testing.json`` (None: its directory)."""

    model = T1

    def __init__(self, tmp_path: Path, backend: str, dep_dir="DepLib", arrange=None):
        self.root = tmp_path
        self.lib = tmp_path / "PartsLib"
        self.dep = tmp_path / "deps" / dep_dir
        for base, files in ((self.lib, _PARTS), (self.dep, _DEP)):
            for rel, text in files.items():
                (base / rel).parent.mkdir(parents=True, exist_ok=True)
                (base / rel).write_text(text, encoding="utf-8")
        entry = arrange(self) if arrange is not None else None
        (tmp_path / "testing.json").write_text(
            json.dumps(
                {
                    "source_path": str(self.lib),
                    "backends": {
                        "OpenModelica": ["/usr/bin/omc", "omc"],
                        "Dymola": ["/usr/local/bin/dymola"],
                    },
                    "dependencies": [entry or str(self.dep)],
                    "test_spec": "test_spec.json",
                    "reference_root": str(tmp_path / "refs"),
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "test_spec.json").write_text(
            json.dumps({"tests": [{"model": T1, "variables": ["x"]}]}),
            encoding="utf-8",
        )
        self._open(tmp_path / "testing.json", backend, tmp_path / "work")

    def x(self) -> float:
        """``x(1)`` of a click's run, which must finish."""
        item = self.click()
        assert item.state is ItemState.FINISHED, item.message
        return self.read(item, "x")[1]["x"]

    def fresh(self) -> float:
        """What a NEW simulator makes of the files as they are: the truth
        a live one's answer is held against."""
        self.session.stop_queue()
        return self.x()


_SIMULATORS = [
    pytest.param(
        "OpenModelica",
        marks=[
            pytest.mark.ompython,
            pytest.mark.skipif(
                not _ompython_available(), reason="needs omc + OMPython"
            ),
        ],
    ),
    pytest.param(
        "Dymola",
        marks=[
            pytest.mark.dymola,
            pytest.mark.skipif(not _dymola_available(), reason="needs Dymola"),
        ],
    ),
]


def _closed(tool: _Live) -> None:
    pids = set(tool.pids)
    tool.close()
    assert not [p for p in pids if _alive(p)]


@pytest.mark.parametrize("backend", _SIMULATORS)
def test_a_sub_package_behind_a_symbolic_link_is_read_again(tmp_path, backend):
    def arrange(parts: Parts) -> None:
        shutil.move(str(parts.lib / "Parts" / "Sub"), str(parts.root / "shared_Sub"))
        os.symlink(parts.root / "shared_Sub", parts.lib / "Parts" / "Sub")

    tool = Parts(tmp_path, backend, arrange=arrange)
    try:
        assert tool.x() == 1111.0
        pids = set(tool.worker().pids)
        _sub(tmp_path / "shared_Sub" / "Deep.mo", "d=1", "d=2")
        assert tool.x() == 1121.0, "the live simulator ran the old sub-package"
        assert set(tool.worker().pids) == pids
        assert tool.fresh() == 1121.0
    finally:
        _closed(tool)


@pytest.mark.parametrize("backend", _SIMULATORS[1:])
def test_a_dependency_whose_path_is_not_its_resolved_one_is_read_again(
    tmp_path, backend
):
    def arrange(parts: Parts) -> str:
        os.symlink(parts.dep, parts.root / "deps" / "link")
        # Absolute, so the config keeps it as written: a link, and `..`.
        return str(parts.root / "deps" / ".." / "deps" / "link")

    tool = Parts(tmp_path, backend, arrange=arrange)
    try:
        (entry,) = tool.session.project.config.dependencies
        assert "/../" in entry and entry.endswith("/link")
        assert tool.x() == 1111.0
        pids = set(tool.worker().pids)
        _sub(tool.dep / "C.mo", "c=1", "c=2")
        assert tool.x() == 2111.0, "the live simulator ran the old dependency"
        assert set(tool.worker().pids) == pids
        assert tool.fresh() == 2111.0
    finally:
        _closed(tool)
