"""`Worker.reload` of the two backends whose live tool reads files again:
what each asks of its tool for which change, and what it refuses.

Over scripted stand-ins for the omc session and the Dymola interface; the
real tools are driven in `test_library_reload_real.py`."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from dstf.authoring.queue_rows import starting_text
from dstf.backends.dymola.persistent_backend import DymolaWorker
from dstf.backends.openmodelica.persistent_backend import (
    OpenModelicaWorker,
    _last_error,
    _load_refused,
    _place,
    _raw_located,
)
from dstf.backends.persistent import LibraryReloadError, Worker
from dstf.backends.pool import StartingWorker
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

    @pytest.mark.parametrize(
        "where", ["no package.mo", "a linked directory", "below a linked directory"]
    )
    def test_a_file_omc_cannot_place_by_its_path_is_the_whole_library(
        self, lib, tmp_path, where
    ):
        """One file is loaded by itself only where its path IS its class:
        every directory from the library down to it holds a `package.mo`
        — omc loads no file of a directory without one, and `loadFile` of
        such a file fails with `Failed to insert class` — and none is a
        symbolic link: the sweep spells a file through the first link that
        reaches its directory, which need not be the one its `within`
        names."""
        if where == "no package.mo":
            (lib / "Examples" / "Loose").mkdir()
            path = lib / "Examples" / "Loose" / "F.mo"
        else:
            shared = tmp_path / "shared"
            (shared / "Sub").mkdir(parents=True)
            for rel in ("package.mo", "F.mo", "Sub/package.mo", "Sub/F.mo"):
                (shared / rel).write_text("", encoding="utf-8")
            os.symlink(shared, lib / "Examples" / "Linked")
            sub = "" if where == "a linked directory" else "Sub/"
            path = lib / "Examples" / "Linked" / (sub + "F.mo")
        path.write_text("", encoding="utf-8")
        worker = _om_worker(lib)
        assert worker._files_to_load_again(LibraryChanges(changed=(path,))) is None
        # ...and with it every other changed file, in the one whole load.
        worker.reload(LibraryChanges(changed=(lib / "Examples" / "A.mo", path)))
        assert worker.session.calls == [
            f'loadFile("{lib}/package.mo")',
            "existClass(Lib)",
        ]

    def test_a_file_two_packages_down_is_loaded_by_itself(self, lib):
        (lib / "Examples" / "Deep").mkdir()
        (lib / "Examples" / "Deep" / "package.mo").write_text("", encoding="utf-8")
        path = lib / "Examples" / "Deep" / "F.mo"
        path.write_text("", encoding="utf-8")
        worker = _om_worker(lib, {"parseFile": _holds("Lib.Examples.Deep.F")})
        worker.reload(LibraryChanges(changed=(path,)))
        assert worker.session.calls == _one_by_one(lib, "Examples/Deep/F.mo")

    def test_a_file_that_is_itself_a_link_is_loaded_by_itself(self, lib, tmp_path):
        """A linked FILE has one spelling — its directory's — and omc loads
        what it points at."""
        kept = tmp_path / "A.mo"
        kept.write_text("", encoding="utf-8")
        path = lib / "Examples" / "A.mo"
        path.unlink()
        os.symlink(kept, path)
        worker = _om_worker(lib, {"parseFile": _holds(A)})
        assert worker._files_to_load_again(LibraryChanges(changed=(path,))) == [path]

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

    def test_every_class_a_dependencys_file_names_must_exist_after_its_load(
        self, lib, tmp_path
    ):
        """A dependency's file may hold more than one top-level class:
        each of them is looked for after the load, not the first alone."""
        single = tmp_path / "deps" / "Single.mo"
        replies = {"parseFile": _holds("Single", "Second")}
        worker = _om_worker(lib, replies, dependencies=[str(single)])
        worker.reload(LibraryChanges(changed=(single,)))
        assert worker.session.calls[2:] == ["existClass(Single)", "existClass(Second)"]
        # The second one missing is a load that failed.
        worker = _om_worker(
            lib,
            {**replies, "existClass(Second)": "false\n"},
            dependencies=[str(single)],
        )
        with pytest.raises(LibraryReloadError) as raised:
            worker.reload(LibraryChanges(changed=(single,)))
        assert str(raised.value) == (
            "Single, Second does not load: loadFile left no class Second"
        )


# -- where a load error is ------------------------------------------------------


def _record(message: str, level: str, kind: str, ident: int, file="", line=0) -> str:
    """One record of omc's `getMessagesStringInternal()`, as omc 1.27
    writes it."""
    return (
        "record OpenModelica.Scripting.ErrorMessage\n"
        "    info = record OpenModelica.Scripting.SourceInfo\n"
        f'    filename = "{file}",\n'
        "    readonly = false,\n"
        f"    lineStart = {line},\n"
        "    columnStart = 7,\n"
        f"    lineEnd = {line},\n"
        "    columnEnd = 7\n"
        "end OpenModelica.Scripting.SourceInfo;,\n"
        f'    message = "{message}",\n'
        f"    kind = .OpenModelica.Scripting.ErrorKind.{kind},\n"
        f"    level = .OpenModelica.Scripting.ErrorLevel.{level},\n"
        f"    id = {ident}\n"
        "end OpenModelica.Scripting.ErrorMessage;"
    )


def _messages(*records: str) -> str:
    return "{" + ", ".join(records) + "}\n"


#: What omc says of a library one of whose files does not parse: that the
#: package failed to load — no file — and then the syntax error, with it.
BROKEN = _messages(
    _record(
        "Failed to load package Lib () using MODELICAPATH /x.",
        "error",
        "scripting",
        184,
    ),
    _record(
        "No viable alternative near token: *",
        "error",
        "syntax",
        2,
        "{root}/Examples/A.mo",
        5,
    ),
)


class _Socket:
    """The session's socket: each expression sent is answered from
    ``replies`` (by its start), the messages after it from ``messages``."""

    def __init__(self, replies: dict[str, str], messages: dict[str, str]):
        self.replies, self.messages = replies, messages
        self.sent: list[str] = []
        self._next = ""

    def send_string(self, expr: str) -> None:
        if expr == "getMessagesStringInternal()":
            asked = self.sent[-1]
            self._next = next(
                (m for key, m in self.messages.items() if asked.startswith(key)), "{}\n"
            )
        else:
            self.sent.append(expr)
            self._next = next(
                (r for key, r in self.replies.items() if expr.startswith(key)), "true\n"
            )

    def recv_string(self) -> str:
        return self._next


class TestWhereAnErrorIs:
    def test_the_error_ompython_raises_is_read_with_its_file_and_line(self):
        text, file, line = _last_error(BROKEN.replace("{root}", "/lib"))
        assert text == "[syntax:error:2] No viable alternative near token: *"
        assert (file, line) == ("/lib/Examples/A.mo", 5)

    def test_warnings_alone_are_no_error_and_a_message_is_unescaped(self):
        warned = _messages(
            _record("Lexer: odd", "warning", "syntax", 9, "/lib/A.mo", 3)
        )
        assert _last_error(warned) is None and _last_error("{}\n") is None
        quoted = _messages(_record('Class \\"A\\" not found', "error", "scripting", 3))
        assert _last_error(quoted) == ('[scripting:error:3] Class "A" not found', "", 0)

    def test_the_place_is_the_path_under_the_library(self, lib):
        assert _place((str(lib / "Examples" / "A.mo"), 5), lib) == "Examples/A.mo:5: "
        assert _place((str(lib / "Examples" / "A.mo"), 0), lib) == "Examples/A.mo: "
        assert _place(("/elsewhere/B.mo", 2), lib) == "/elsewhere/B.mo:2: "
        assert _place(None, lib) == ""

    def test_a_reply_and_its_error_are_read_on_the_socket(self, lib):
        expr = f'loadFile("{lib}/package.mo")'
        socket = _Socket(
            {"loadFile": "false\n"}, {"loadFile": BROKEN.replace("{root}", str(lib))}
        )
        session = SimpleNamespace(omc_zmq=socket)
        assert _raw_located(session, expr) == (
            "",
            "[syntax:error:2] No viable alternative near token: *",
            (f"{lib}/Examples/A.mo", 5),
        )
        assert socket.sent == [expr]
        # No error: the reply, and no place.
        fine = SimpleNamespace(omc_zmq=_Socket({"parseFile": "{Lib.A}\n"}, {}))
        assert _raw_located(fine, 'parseFile("x")') == ("{Lib.A}", None, None)

    def test_a_refused_load_is_its_error_led_by_its_place(self, lib):
        """The one reading of a load omc refused — a worker's start and its
        reload: None when omc took it, else the error with the file and
        line under the root, and omc's answer when it gave no error."""
        expr = f'loadFile("{lib}/package.mo")'
        broken = BROKEN.replace("{root}", str(lib))
        refused = SimpleNamespace(
            omc_zmq=_Socket({"loadFile": "false\n"}, {"loadFile": broken})
        )
        why = "[syntax:error:2] No viable alternative near token: *"
        assert _load_refused(refused, expr, lib) == f"Examples/A.mo:5: {why}"
        # No root (a library omc found by name): the file as omc names it.
        assert _load_refused(refused, expr, None) == f"{lib}/Examples/A.mo:5: {why}"
        taken = SimpleNamespace(omc_zmq=_Socket({}, {}))
        assert _load_refused(taken, expr, lib) is None
        silent = SimpleNamespace(omc_zmq=_Socket({"loadFile": "false\n"}, {}))
        assert _load_refused(silent, expr, lib) == "omc answered false"

    def test_a_session_without_the_socket_is_asked_the_usual_way(self, lib):
        expr = f'loadFile("{lib}/package.mo")'
        message = "[syntax:error:2] No viable alternative near token: ;"
        session = _Omc({expr: _omc_error(expr, message)})
        assert _raw_located(session, expr) == ("", message, None)
        assert _raw_located(_Omc(), "existClass(Lib)") == ("true", None, None)

    def test_a_whole_load_that_fails_names_the_file_and_the_line(self, lib):
        """`Lib does not load: [syntax:error:2] …` said that a library of
        thousands of files has a syntax error, and not where."""
        worker = _om_worker(lib)
        worker.session = SimpleNamespace(
            omc_zmq=_Socket(
                {"loadFile": "false\n"},
                {"loadFile": BROKEN.replace("{root}", str(lib))},
            )
        )
        with pytest.raises(LibraryReloadError) as raised:
            worker.reload(None)
        assert str(raised.value) == (
            "Lib does not load: Examples/A.mo:5: [syntax:error:2] No viable "
            "alternative near token: *"
        )

    def test_one_file_that_does_not_parse_says_its_line(self, lib):
        path = lib / "Examples" / "A.mo"
        worker = _om_worker(lib)
        worker.session = SimpleNamespace(
            omc_zmq=_Socket(
                {"parseFile": "{}\n"},
                {
                    "parseFile": _messages(
                        _record("Missing token: ;", "error", "syntax", 2, str(path), 7)
                    )
                },
            )
        )
        with pytest.raises(LibraryReloadError) as raised:
            worker.reload(LibraryChanges(changed=(path,)))
        assert str(raised.value) == (
            "Lib does not load: Examples/A.mo:7: [syntax:error:2] Missing token: ;"
        )


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
        # Quoted, as OpenModelica's loadFile is: the panel cuts a quoted
        # path to its last part, and showed this one whole.
        assert worker._step == f'openModel("{lib}/package.mo")'
        shown = starting_text(StartingWorker(0, worker._step, 12.0, reload=True))
        assert shown == (
            'worker 1 reading the library — openModel("…/package.mo") · 12 s'
        )

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

    @pytest.mark.parametrize(
        "form", ["its directory", "its package.mo", "a one-file library"]
    )
    def test_a_dependency_is_opened_by_its_file_whichever_way_it_is_given(
        self, lib, tmp_path, form
    ):
        """`dependencies` takes a library's directory, its `package.mo` or
        a one-file library `Name.mo`. Dymola was handed
        `<entry>/package.mo` for all three: `…/DepLib.mo/package.mo`, and
        no worker could start."""
        from dstf.discovery.library_state import dependency_file

        directory = tmp_path / "deps" / "Other"
        directory.mkdir(parents=True)
        entry, opened = {
            "its directory": (directory, directory / "package.mo"),
            "its package.mo": (directory / "package.mo", directory / "package.mo"),
            "a one-file library": (
                tmp_path / "deps" / "One.mo",
                tmp_path / "deps" / "One.mo",
            ),
        }[form]
        assert dependency_file(str(entry)) == opened
        dymola = _Dymola()
        worker = _dymola_worker(lib, dymola, [str(entry)])
        worker.reload(None)
        assert [path for path, _ in dymola.opened] == [
            str(opened),
            str(lib / "package.mo"),
        ]
        # The batch path's startup script opens the same file.
        from dstf.backends.dymola.backend import DymolaConfig, _generate_startup_mos

        config = SimpleNamespace(
            work_dir=tmp_path,
            dependencies=[str(entry)],
            library_dir=lib,
            backend_setup=[],
        )
        script = _generate_startup_mos(config, DymolaConfig()).read_text("utf-8")
        assert f'openModel("{opened.as_posix()}");' in script
        assert ".mo/package.mo" not in script

    def test_a_dependency_that_does_not_open_is_named_as_it_is_written(
        self, lib, tmp_path
    ):
        one = tmp_path / "deps" / "One.mo"
        worker = _dymola_worker(lib, _Dymola(fails=str(one)), [str(one)])
        with pytest.raises(LibraryReloadError) as raised:
            worker.reload(None)
        assert str(raised.value).startswith("One.mo does not load: ")

    def test_a_library_dymola_does_not_open_fails_with_its_message(self, lib):
        worker = _dymola_worker(lib, _Dymola(fails=str(lib)))
        with pytest.raises(LibraryReloadError) as raised:
            worker.reload(None)
        assert (
            str(raised.value) == "Lib does not load: Error: the file is not a package"
        )
