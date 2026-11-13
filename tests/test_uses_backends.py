"""`Backend.uses` of the simulators that answer it, over fakes of their
interfaces: what each is asked, where its files go, what a test it could
not answer for becomes, and that it is closed whatever happens."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_test_model as _tm
from dstf.config import Config

FIXTURES = Path(__file__).parent / "fixtures" / "uses"
LIB = "ModelicaTestingLib"
SIMPLE = f"{LIB}.Examples.SimpleTest"
#: What the bundled library's SimpleTest uses, as both tools write it.
SIMPLE_USES = frozenset(
    {
        LIB,
        f"{LIB}.Examples",
        SIMPLE,
        f"{LIB}.Components",
        f"{LIB}.Components.UnitTests",
    }
)


@pytest.fixture(autouse=True)
def _a_made_up_pid_is_no_process(monkeypatch):
    """As tests/test_openmodelica_persistent.py's: a fake session's PID is
    made up — never watched as the simulator, never killed (on this machine
    the number may be somebody's real omc)."""
    monkeypatch.setattr(
        "dstf.backends.persistent.process_gone", lambda pids, name_hint=None: False
    )
    monkeypatch.setattr(
        "dstf.backends.persistent.kill_process_tree", lambda *a, **kw: None
    )


def _config(tmp_path: Path, backend: str, dependencies=()) -> Config:
    """A project of one library, `ModelicaTestingLib`, whose work dir does
    not exist — and must not, afterwards."""
    root = tmp_path / "project"
    lib = root / LIB
    lib.mkdir(parents=True)
    (lib / "package.mo").write_text(f"package {LIB}\nend {LIB};\n")
    config = root / "testing.json"
    config.write_text(
        json.dumps(
            {
                "source_path": LIB,
                "backend": backend,
                "dependencies": list(dependencies),
            }
        )
    )
    return Config(config_file=config, work_dir=root / "work")


def _written(fixture: str):
    """A `save` that writes the real tool's file of SimpleTest."""

    def save(file: str) -> bool:
        Path(file).write_text((FIXTURES / fixture).read_text(encoding="utf-8"))
        return True

    return save


# --------------------------------------------------------------------------
# Dymola
# --------------------------------------------------------------------------


def _dymola(monkeypatch, config: Config, save, error_log="") -> list:
    """A PersistentDymolaBackend over a fake interface whose
    ``saveTotalModel(file, ...)`` is ``save(file)``. Returns the
    interfaces it made (one per worker)."""
    from dstf.backends.dymola import persistent_backend as pr

    made: list = []

    class Interface:
        def __init__(self, showwindow=False, dymolapath=None):
            self.saved: list[tuple] = []
            self.closed = False
            self.cwd = "<where dstf was started>"
            self.visited: list[str] = []
            made.append(self)

        def cd(self, path):
            self.cwd = str(path)
            self.visited.append(self.cwd)
            return True

        def openModel(self, path, mustRead=True, changeDirectory=True):
            # Dymola's own default: it moves into the file's directory
            if changeDirectory:
                self.cwd = str(Path(path).parent)
            return True

        def ExecuteCommand(self, cmd):
            return True

        def getLastErrorLog(self):
            return error_log

        def saveTotalModel(self, file, model, **kw):
            self.saved.append((file, model, kw, self.cwd))
            return save(file)

        def close(self):
            self.closed = True

    monkeypatch.setattr(pr, "load_dymola_interface", lambda found=None: Interface)
    backend = pr.PersistentDymolaBackend(config)
    return backend, made


def test_dymola_saves_the_total_model_in_a_temporary_directory(monkeypatch, tmp_path):
    config = _config(tmp_path, "Dymola")
    backend, made = _dymola(monkeypatch, config, _written("dymola_SimpleTest.mo"))

    # the wrapper model `ModelicaTestingLib_Examples_SimpleTest` is not the
    # project's
    assert backend.uses([_tm(SIMPLE)]) == {SIMPLE: SIMPLE_USES}

    (interface,) = made
    ((file, model, kw, stood_in),) = interface.saved
    assert (model, kw) == (SIMPLE, {"skipStandard": True})
    # an absolute path, in the directory Dymola stood in when asked — not
    # the library's, where loading it had left Dymola
    assert Path(file).is_absolute()
    assert stood_in == str(Path(file).parent)
    assert stood_in != str(config.library_dir)
    # that directory is gone; Dymola, which writes its logs where it
    # stands, was never in the project's work dir, and it was never made
    assert not Path(stood_in).exists()
    assert str(config.work_dir) not in interface.visited
    assert not config.work_dir.exists()
    assert interface.closed


def test_dymola_a_test_it_did_not_save_gets_its_reason_and_the_next_is_asked(
    monkeypatch, tmp_path
):
    gone = f"{LIB}.Examples.Gone"

    def save(file):
        return Path(file).name != "0.mo" and _written("dymola_SimpleTest.mo")(file)

    backend, made = _dymola(
        monkeypatch,
        _config(tmp_path, "Dymola"),
        save,
        error_log="Error: class\n   ModelicaTestingLib.Examples.Gone   not found.\n",
    )
    assert backend.uses([_tm(gone), _tm(SIMPLE)]) == {
        gone: "Error: class ModelicaTestingLib.Examples.Gone not found.",
        SIMPLE: SIMPLE_USES,
    }
    assert [m for _, m, _, _ in made[0].saved] == [gone, SIMPLE]


def test_dymola_a_reason_it_did_not_give_is_said_and_a_long_one_is_cut(
    monkeypatch, tmp_path
):
    gone = f"{LIB}.Examples.Gone"
    backend, _ = _dymola(monkeypatch, _config(tmp_path, "Dymola"), lambda f: False)
    (reason,) = backend.uses([_tm(gone)]).values()
    assert reason == f"Dymola did not save {gone} and said nothing of why"

    backend, _ = _dymola(
        monkeypatch,
        _config(tmp_path / "long", "Dymola"),
        lambda f: False,
        error_log="x" * 1000,
    )
    (reason,) = backend.uses([_tm(gone)]).values()
    assert reason == "x" * 299 + "…"


def test_dymola_is_closed_when_it_stops_answering(monkeypatch, tmp_path):
    def save(file):
        raise ConnectionError("Dymola is gone")

    config = _config(tmp_path, "Dymola")
    backend, made = _dymola(monkeypatch, config, save)
    with pytest.raises(ConnectionError, match="Dymola is gone"):
        backend.uses([_tm(SIMPLE)])
    (interface,) = made
    assert interface.closed
    assert not Path(interface.saved[0][0]).parent.exists()
    assert not config.work_dir.exists()


def test_a_path_dependency_s_classes_are_the_project_s(monkeypatch, tmp_path):
    """A dependency given as a path is named by the package its file
    declares, not by its directory; a library loaded by name is not the
    project's, nor is the wrapper model."""
    dep = tmp_path / "Dep 1.0"
    dep.mkdir()
    (dep / "package.mo").write_text("package Dep\n  model M end M;\nend Dep;\n")
    one = tmp_path / "One.mo"
    one.write_text("package One\n  type T = Real;\nend One;\n")
    total = f"""package {LIB}
  model Test
    Dep.M m;
    One.T t;
  end Test;
end {LIB};
package Dep
  model M end M;
end Dep;
package One
  type T = Real;
end One;
package Other
  model O end O;
end Other;
model {LIB}_Test
  extends {LIB}.Test;
end {LIB}_Test;
"""

    def save(file):
        Path(file).write_text(total)
        return True

    config = _config(tmp_path, "Dymola", dependencies=[str(dep), str(one), "Other"])
    backend, _ = _dymola(monkeypatch, config, save)
    assert backend.uses([_tm(f"{LIB}.Test")]) == {
        f"{LIB}.Test": frozenset({LIB, f"{LIB}.Test", "Dep", "Dep.M", "One", "One.T"})
    }


# --------------------------------------------------------------------------
# OpenModelica
# --------------------------------------------------------------------------


def _openmodelica(monkeypatch, config: Config, save):
    """A PersistentOpenModelicaBackend over a FakeSession whose answer to
    ``saveTotalModelDebug(...)`` is ``save(file)``: a bool, a str (the raw
    reply), None (no reply) or an exception (what OMPython raises)."""
    from dstf.backends.openmodelica import persistent_backend as pr
    from test_openmodelica_persistent import FakeSession

    def reply(expr: str):
        return save(expr.split('"')[1])

    session = FakeSession({"saveTotalModelDebug(": reply})
    monkeypatch.setattr(pr, "load_omc_session", lambda: lambda: session)
    return pr.PersistentOpenModelicaBackend(config), session


def _saved(session) -> list[str]:
    return [c for c in session.calls if c.startswith("saveTotalModelDebug(")]


def test_openmodelica_saves_the_total_model_in_a_temporary_directory(
    monkeypatch, tmp_path
):
    config = _config(tmp_path, "OpenModelica")
    backend, session = _openmodelica(
        monkeypatch, config, _written("openmodelica_SimpleTest.mo")
    )

    # As the worker asks it, with the Modelica Standard Library loaded: omc
    # writes `package Modelica` at top level between the library and its
    # wrapper model `SimpleTest_total`; neither is the project's. It keeps
    # an emptied `Icons` package of the library the model does not use.
    assert backend.uses([_tm(SIMPLE)]) == {SIMPLE: SIMPLE_USES | {f"{LIB}.Icons"}}

    (sent,) = _saved(session)
    file = Path(sent.split('"')[1])
    assert sent == f'saveTotalModelDebug("{file.as_posix()}", {SIMPLE})'
    assert file.is_absolute() and not file.parent.exists()
    assert not config.work_dir.exists()
    assert session.calls[-1] == "quit()"


def test_openmodelica_an_error_it_logged_is_that_test_s_reason(monkeypatch, tmp_path):
    """OMPython raises for an error omc logged, and its reading of the
    messages empties `getErrorString()`: the exception's text is the
    reason. The next test is still asked."""
    gone = f"{LIB}.Examples.Gone"

    def save(file):
        if file.endswith("/0.mo"):
            return RuntimeError(
                f"[OMC log for 'sendExpression(saveTotalModelDebug(...), False)']: "
                f"[scripting:error:0]\n  Class {gone} not found in scope <top>."
            )
        return _written("openmodelica_SimpleTest.mo")(file)

    backend, session = _openmodelica(
        monkeypatch, _config(tmp_path, "OpenModelica"), save
    )
    answers = backend.uses([_tm(gone), _tm(SIMPLE)])
    assert answers[gone] == (
        f"[scripting:error:0] Class {gone} not found in scope <top>."
    )
    assert answers[SIMPLE] == SIMPLE_USES | {f"{LIB}.Icons"}
    assert len(_saved(session)) == 2


@pytest.mark.parametrize(
    ("reply", "said"),
    [
        # omc 1.27.2, for a class it does not hold: no reply, no message
        (None, "nothing"),
        (False, "false"),
    ],
)
def test_openmodelica_no_answer_and_no_message_still_gives_a_reason(
    monkeypatch, tmp_path, reply, said
):
    gone = f"{LIB}.Examples.Gone"
    backend, _ = _openmodelica(
        monkeypatch, _config(tmp_path, "OpenModelica"), lambda file: reply
    )
    assert backend.uses([_tm(gone)]) == {
        gone: f"omc answered {said} for saveTotalModelDebug of {gone}"
    }


def test_openmodelica_is_closed_when_the_answer_cannot_be_read(monkeypatch, tmp_path):
    """omc said it saved, and wrote nothing: the run stops, omc is closed."""
    config = _config(tmp_path, "OpenModelica")
    backend, session = _openmodelica(monkeypatch, config, lambda file: True)
    with pytest.raises(FileNotFoundError):
        backend.uses([_tm(SIMPLE)])
    assert session.calls[-1] == "quit()"
    assert not config.work_dir.exists()
