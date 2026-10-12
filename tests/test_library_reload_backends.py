"""`Worker.reload` of the two backends whose live tool reads files again:
what each asks of its tool for which change, and what it refuses.

Over scripted stand-ins for the omc session and the Dymola interface; the
real tools are driven in `test_library_reload_real.py`."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from dstf.backends.dymola.persistent_backend import DymolaWorker
from dstf.backends.openmodelica.persistent_backend import OpenModelicaWorker
from dstf.backends.persistent import LibraryReloadError, Worker
from dstf.discovery.library_state import LibraryChanges, LibraryState, source_stamps


class _Omc:
    """Answers by the longest matching prefix: a raw reply, or an
    exception to raise (omc reported an error). ``true`` otherwise."""

    def __init__(self, replies: dict[str, object] | None = None):
        self.replies = replies or {}
        self.calls: list[str] = []

    def sendExpression(self, expr: str, parsed: bool = True):
        assert parsed is False
        self.calls.append(expr)
        matches = [key for key in self.replies if expr.startswith(key)]
        reply = self.replies[max(matches, key=len)] if matches else "true\n"
        if isinstance(reply, BaseException):
            raise reply
        return reply


def _omc_error(expr: str, message: str) -> Exception:
    """What OMPython raises for an error omc logged."""
    return RuntimeError(f"[OMC log for 'sendExpression({expr}, False)']: {message}")


@pytest.fixture
def lib(tmp_path) -> Path:
    root = tmp_path / "Lib"
    (root / "Examples").mkdir(parents=True)
    for rel in ("package.mo", "Examples/package.mo", "Examples/A.mo", "Examples/B.mo"):
        (root / rel).write_text("", encoding="utf-8")
    return root


def _om_worker(lib: Path, replies=None, dependencies=()) -> OpenModelicaWorker:
    config = SimpleNamespace(
        library_dir=lib, library_name="Lib", dependencies=list(dependencies)
    )
    worker = OpenModelicaWorker(0, config, SimpleNamespace(), None)
    worker.session = _Omc(replies)
    # A whole load of this library is slow: one file at a time is cheaper.
    worker._library_load_seconds = 60.0
    return worker


def _holds(*names: str) -> str:
    return "{" + ", ".join(names) + "}\n"


A = "Lib.Examples.A"


def _one_by_one(lib: Path, rel: str) -> list[str]:
    file = lib / rel
    name = "Lib." + rel[: -len(".mo")].replace("/", ".")
    return [
        f'parseFile("{file}")',
        f"deleteClass({name})",
        f'loadFile("{file}")',
        f"existClass({name})",
    ]


class TestOpenModelica:
    def test_it_reads_in_place(self, lib):
        assert _om_worker(lib).reloads_in_place

    def test_a_changed_file_is_parsed_deleted_loaded_and_checked(self, lib):
        worker = _om_worker(lib, {"parseFile": _holds(A)})
        worker.reload(LibraryChanges(changed=(lib / "Examples" / "A.mo",)))
        assert worker.session.calls == _one_by_one(lib, "Examples/A.mo")

    def test_nothing_known_of_what_changed_is_the_whole_library(self, lib):
        worker = _om_worker(lib)
        worker.reload(None)
        assert worker.session.calls == [
            f'loadFile("{lib}/package.mo")',
            "existClass(Lib)",
        ]

    @pytest.mark.parametrize(
        "changes",
        [
            {"changed": ("Examples/package.mo",)},
            {"changed": ("Examples/A.mo", "package.order")},
            {"changed": ("Examples/A.mo",), "added": ("Examples/New.mo",)},
            {"removed": ("Examples/B.mo",)},
        ],
        ids=["a package.mo", "a package.order", "an added file", "a removed file"],
    )
    def test_what_one_file_cannot_say_is_the_whole_library(self, lib, changes):
        worker = _om_worker(lib)
        worker.reload(
            LibraryChanges(
                **{k: tuple(lib / rel for rel in v) for k, v in changes.items()}
            )
        )
        assert worker.session.calls == [
            f'loadFile("{lib}/package.mo")',
            "existClass(Lib)",
        ]

    def test_more_files_than_a_whole_load_costs_is_the_whole_library(self, lib):
        changes = LibraryChanges(
            changed=(lib / "Examples" / "A.mo", lib / "Examples" / "B.mo")
        )
        worker = _om_worker(lib)
        worker._library_load_seconds = 2 * worker._FILE_LOAD_SECONDS - 0.01
        worker.reload(changes)
        assert worker.session.calls[0] == f'loadFile("{lib}/package.mo")'
        # At the price of two files, two files are read.
        worker = _om_worker(lib)
        worker._library_load_seconds = 2 * worker._FILE_LOAD_SECONDS
        assert worker._files_to_load_again(changes) == list(changes.changed)

    def test_a_whole_load_is_timed_for_the_next_choice(self, lib):
        worker = _om_worker(lib)
        worker.reload(None)
        assert 0.0 <= worker._library_load_seconds < 5.0

    def test_a_file_that_does_not_parse_fails_with_omcs_message_and_deletes_nothing(
        self, lib
    ):
        expr = f'parseFile("{lib}/Examples/A.mo")'
        message = "[syntax:error:2] No viable alternative near token: ;"
        worker = _om_worker(lib, {"parseFile": _omc_error(expr, message)})
        with pytest.raises(LibraryReloadError) as raised:
            worker.reload(LibraryChanges(changed=(lib / "Examples" / "A.mo",)))
        assert str(raised.value) == f"Lib does not load: Examples/A.mo: {message}"
        # omc still holds the class of the file as it was.
        assert worker.session.calls == [expr]

    @pytest.mark.parametrize(
        ("holds", "said"),
        [
            (("Lib.Examples.Renamed",), "it holds Lib.Examples.Renamed, not " + A),
            ((A, "Lib.Examples.Extra"), f"it holds {A}, Lib.Examples.Extra, not {A}"),
            (("Lib.Other.A",), "it holds Lib.Other.A, not " + A),
            ((), "it holds no class, not " + A),
        ],
        ids=["renamed", "a second class", "another within", "empty"],
    )
    def test_a_file_that_does_not_hold_exactly_its_class_is_refused(
        self, lib, holds, said
    ):
        worker = _om_worker(lib, {"parseFile": _holds(*holds)})
        with pytest.raises(LibraryReloadError) as raised:
            worker.reload(LibraryChanges(changed=(lib / "Examples" / "A.mo",)))
        assert str(raised.value) == f"Lib does not load: Examples/A.mo: {said}"
        assert len(worker.session.calls) == 1

    @pytest.mark.parametrize("how", ["the load fails", "the class is not there"])
    def test_a_load_that_fails_after_the_delete_says_so(self, lib, how):
        """omc then holds neither the file's old class nor a new one. The
        worker keeps no account of it: its caller asks the next reload
        for everything (`test_library_reload.py`)."""
        path = lib / "Examples" / "A.mo"
        replies: dict[str, object] = {"parseFile": _holds(A)}
        if how == "the load fails":
            replies[f'loadFile("{path}")'] = _omc_error("loadFile", "no such package")
            said = "no such package"
        else:
            replies["existClass"] = "false\n"
            said = f"loadFile left no class {A}"
        worker = _om_worker(lib, replies)
        with pytest.raises(LibraryReloadError) as raised:
            worker.reload(LibraryChanges(changed=(path,)))
        assert str(raised.value) == f"Lib does not load: Examples/A.mo: {said}"

    def test_a_whole_load_that_fails_says_so_with_omcs_message(self, lib):
        expr = f'loadFile("{lib}/package.mo")'
        message = "[syntax:error:2] No viable alternative near token: ;"
        worker = _om_worker(lib, {expr: _omc_error(expr, message)})
        with pytest.raises(LibraryReloadError) as raised:
            worker.reload(None)
        assert str(raised.value) == f"Lib does not load: {message}"

    def test_a_dependency_given_as_a_path_is_loaded_whole_when_a_file_of_it_changed(
        self, lib, tmp_path
    ):
        # Its directory does not carry its package's name: the class that
        # must exist after the load is the one its package.mo names.
        other = tmp_path / "deps" / "Other 1.0.0"
        other.mkdir(parents=True)
        dep = [
            f'parseFile("{other}/package.mo")',
            f'loadFile("{other}/package.mo")',
            "existClass(Other)",
        ]
        worker = _om_worker(
            lib,
            {dep[0]: _holds("Other"), "parseFile": _holds(A)},
            dependencies=["Modelica", str(other)],
        )
        worker.reload(LibraryChanges(changed=(other / "Sub" / "X.mo",)))
        assert worker.session.calls == dep
        # A change in the library alone leaves the dependency as it is.
        worker.session.calls.clear()
        worker.reload(LibraryChanges(changed=(lib / "Examples" / "A.mo",)))
        assert worker.session.calls == _one_by_one(lib, "Examples/A.mo")
        # Nothing known: every path dependency, then the library.
        worker.session.calls.clear()
        worker.reload(None)
        assert worker.session.calls == [
            *dep,
            f'loadFile("{lib}/package.mo")',
            "existClass(Lib)",
        ]

    @pytest.mark.parametrize(
        ("reply", "said"),
        [
            (_holds(), "Other 1.0.0 does not load: it holds no class"),
            (
                _omc_error("parseFile", "[syntax:error:2] Missing token: ;"),
                "Other 1.0.0 does not load: [syntax:error:2] Missing token: ;",
            ),
            ("load", "Other does not load: no such package"),
            ("exist", "Other does not load: loadFile left no class Other"),
        ],
        ids=["empty", "does not parse", "does not load", "left no class"],
    )
    def test_a_dependency_that_does_not_load_says_so(self, lib, tmp_path, reply, said):
        other = tmp_path / "deps" / "Other 1.0.0"
        replies: dict[str, object] = {"parseFile": _holds("Other")}
        if reply == "load":
            replies["loadFile"] = _omc_error("loadFile", "no such package")
        elif reply == "exist":
            replies["existClass"] = "false\n"
        else:
            replies["parseFile"] = reply
        worker = _om_worker(lib, replies, dependencies=[str(other)])
        with pytest.raises(LibraryReloadError) as raised:
            worker.reload(LibraryChanges(changed=(other / "X.mo",)))
        assert str(raised.value) == said
        # The library is not loaded over a dependency that did not load.
        assert not any(str(lib) in call for call in worker.session.calls)

    def test_a_dependency_that_is_one_file_is_loaded_when_that_file_changed(
        self, lib, tmp_path
    ):
        single = tmp_path / "deps" / "Single.mo"
        worker = _om_worker(
            lib, {"parseFile": _holds("Single")}, dependencies=[str(single)]
        )
        worker.reload(LibraryChanges(changed=(single,)))
        assert worker.session.calls == [
            f'parseFile("{single}")',
            f'loadFile("{single}")',
            "existClass(Single)",
        ]


class _Dymola:
    def __init__(self, fails: str | None = None):
        self.opened: list[tuple[str, bool]] = []
        self.fails = fails

    def openModel(self, path, mustRead=True, changeDirectory=True):
        assert mustRead is True
        self.opened.append((path, changeDirectory))
        return self.fails is None or not path.startswith(self.fails)

    def getLastErrorLog(self):
        return "Error: the file is not a package\n"


def _dymola_worker(lib: Path, dymola: _Dymola, dependencies=()) -> DymolaWorker:
    config = SimpleNamespace(library_dir=lib, dependencies=list(dependencies))
    worker = DymolaWorker(0, config, SimpleNamespace(), None)
    worker.dymola = dymola
    return worker


class TestDymola:
    def test_it_reads_in_place(self, lib):
        assert _dymola_worker(lib, _Dymola()).reloads_in_place
        assert DymolaWorker.reload is not Worker.reload

    def test_the_library_is_opened_again_without_changing_directory(self, lib):
        dymola = _Dymola()
        worker = _dymola_worker(lib, dymola)
        worker.reload(LibraryChanges(changed=(lib / "Examples" / "A.mo",)))
        assert dymola.opened == [(str(lib / "package.mo"), False)]
        assert worker._step == f"openModel({lib / 'package.mo'})"

    def test_a_dependency_is_opened_first_when_a_file_of_it_changed(
        self, lib, tmp_path
    ):
        other, third = tmp_path / "Other", tmp_path / "Third"
        dymola = _Dymola()
        worker = _dymola_worker(lib, dymola, [str(other), str(third)])
        worker.reload(LibraryChanges(removed=(other / "X.mo",)))
        assert [path for path, _ in dymola.opened] == [
            str(other / "package.mo"),
            str(lib / "package.mo"),
        ]
        dymola.opened.clear()
        worker.reload(None)  # nothing known: every one of them
        assert [path for path, _ in dymola.opened] == [
            str(other / "package.mo"),
            str(third / "package.mo"),
            str(lib / "package.mo"),
        ]
        assert all(change is False for _, change in dymola.opened)

    @pytest.mark.parametrize("how", ["a symbolic link", "a path with .."])
    def test_a_dependency_is_found_by_the_spelling_the_sweep_gave_its_files(
        self, lib, tmp_path, how
    ):
        """The changes a worker is handed are the SWEEP's paths: a
        dependency whose configured path is not its resolved one (a link;
        an absolute path with ``..``, which the config keeps as written)
        is still the one a changed file belongs to."""
        real = tmp_path / "deps" / "Other"
        real.mkdir(parents=True)
        (real / "X.mo").write_text("", encoding="utf-8")
        if how == "a symbolic link":
            entry = tmp_path / "deps" / "link"
            os.symlink(real, entry)
        else:
            entry = tmp_path / "deps" / ".." / "deps" / "Other"
        dymola = _Dymola()
        worker = _dymola_worker(lib, dymola, [str(entry)])
        config = SimpleNamespace(
            source_type="modelica", library_dir=lib, dependencies=[str(entry)]
        )
        before = LibraryState(1, source_stamps(config))
        (real / "X.mo").write_text("package X end X;", encoding="utf-8")
        changes = LibraryState(2, source_stamps(config)).changes_since(before)
        assert changes.changed == (entry / "X.mo",)
        worker.reload(changes)
        assert [path for path, _ in dymola.opened] == [
            str(real / "package.mo"),
            str(lib / "package.mo"),
        ]

    def test_a_library_dymola_does_not_open_fails_with_its_message(self, lib):
        worker = _dymola_worker(lib, _Dymola(fails=str(lib)))
        with pytest.raises(LibraryReloadError) as raised:
            worker.reload(None)
        assert (
            str(raised.value) == "Lib does not load: Error: the file is not a package"
        )
