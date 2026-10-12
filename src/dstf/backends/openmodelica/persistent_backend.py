"""Persistent-worker OpenModelica backend using OMPython (OMCSessionZMQ).

Unlike the default batched backend (which spawns one ``omc`` subprocess per
test driven by a generated ``simulate.mos``), this backend keeps N long-lived
``OMCSessionZMQ`` processes alive for the whole run. Each worker loads MSL
+ the library once and then pulls tests off a shared queue.

Benefits:
  - MSL + library load paid once per worker (major win for TRANSFORM —
    per-test compile was ~50–100s in the 2026-04-22 sweep; amortizing
    library load gives a 5–10× wall-time cut)
  - Per-test granularity: start/finish events fire as each test transitions
  - Natural work-stealing: whichever worker finishes first grabs the next test
  - Per-test timeout isolation: a hung test kills one worker, not the batch

The default CLI mode selects this backend; pass ``--batch`` to opt out and
use the subprocess-per-test backend, which doesn't require OMPython. There
is no automatic fallback: if OMPython can't be imported, ``preflight``
raises and the CLI aborts with a message naming both fixes (see
``backends.select_backend``).

Phase labels are cosmetic — OM's ``simulate()`` bundles build + run in one
call with no mid-call progress hook, so we emit ``translating`` before the
call and ``simulating`` after (matching the existing batch backend pattern).
Per-phase wall timings are still accurate in the final report because they
come from the returned ``SimulationResult`` record.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path

from ...config import Config
from ...discovery.library_state import LibraryChanges, dependency_root, under
from ...discovery.test_registry import TestModel
from ..common.mat_reader import mat_reaches_time
from ..common.proc_kill import live_children
from ..persistent import LibraryReloadError, PersistentBackendBase, Worker
from ..types import Capability, TestRunResult
from .backend import OpenModelicaBackend, OpenModelicaConfig
from .log_parser import _TIMING_KEYS, ParsedOmcOutput, parse_omc_stdout
from .mos_generator import build_simulate_args, classify_dependency
from .session_loader import load_omc_session

logger = logging.getLogger(__name__)


# OMPython's pyparsing grammar (OMTypedParser) is a module-level singleton
# and is NOT thread-safe — concurrent ``sendExpression(parsed=True)`` calls
# across workers corrupt parse state and raise ``TypeError: convertString()
# missing 1 required positional argument``. We sidestep the issue entirely
# by always calling ``sendExpression(expr, parsed=False)`` and parsing the
# raw OMC wire format ourselves with thread-safe helpers. The ZMQ layer is
# per-instance and safe; only the pyparsing step was the bottleneck.


def _raw_exec_bool(session, expr: str) -> bool:
    """Send an expression, parse raw OMC response as bool.

    OMC returns ``"true\\n"`` / ``"false\\n"`` on the wire; anything else
    (or an exception from the ZMQ layer) is treated as false.
    """
    try:
        raw = session.sendExpression(expr, parsed=False)
    except Exception:
        return False
    return (raw or "").strip() == "true"


def _raw_reply(session, expr: str) -> tuple[str, str | None]:
    """Send an expression: ``(omc's raw reply, None)``, or ``("", omc's
    message)`` when the call reported an error. OMPython raises with the
    error's text then (and that empties ``getErrorString()``), so the
    exception's text IS the message — less OMPython's own prefix."""
    try:
        raw = session.sendExpression(expr, parsed=False)
    except Exception as exc:
        # "[OMC log for 'sendExpression(...)']: <message>"
        text = str(exc).split("']: ", 1)[-1].strip()
        return "", text or type(exc).__name__
    return (raw or "").strip(), None


def _raw_get_error_string(session) -> str:
    """Call ``getErrorString()`` and unwrap the quoted string.

    OMC's wire format for a string is ``"<escaped>"\\n`` — we strip the
    surrounding quotes and undo ``\\"`` / ``\\\\`` escapes.
    """
    try:
        raw = session.sendExpression("getErrorString()", parsed=False)
    except Exception:
        return ""
    s = (raw or "").strip()
    if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
        s = s[1:-1]
    return s.replace('\\"', '"').replace("\\\\", "\\")


def _raw_send_simulate(session, expr: str) -> tuple[dict | None, str]:
    """Run ``simulate(...)``, parse the echoed record via the regex parser.

    Returns ``(record_dict_or_None, raw_string)``. Uses the existing
    :func:`parse_omc_stdout` (pure ``re`` module — thread-safe) instead of
    OMPython's pyparsing grammar. OMC's raw wire format for a record is
    byte-identical to what omc REPL-echoes as stdout in batch mode.
    """
    raw = session.sendExpression(expr, parsed=False) or ""
    parsed = parse_omc_stdout(raw)
    if not parsed.result_file and not parsed.messages and not parsed.timings:
        return None, raw
    rec: dict = {
        "resultFile": parsed.result_file,
        "messages": parsed.messages,
    }
    # Back-translate our internal timing keys to OMC's field names so the
    # rest of the pipeline (``_parsed_from_record``) can treat this dict
    # identically to OMPython's parsed-mode output.
    _reverse_timing = {v: k for k, v in _TIMING_KEYS.items()}
    if parsed.timings is not None:
        for our_key, val in parsed.timings.items():
            om_key = _reverse_timing.get(our_key)
            if om_key is not None:
                rec[om_key] = val
    return rec, raw


def _parsed_from_record(
    rec: dict | None,
    error_notices: list[str],
) -> ParsedOmcOutput:
    """Build a :class:`ParsedOmcOutput` from OMPython's returned dict.

    OMPython converts OM records to Python dicts; this bypasses the regex
    parsing the batch path does on textual stdout, producing the same
    structured result.
    """
    if not rec or not isinstance(rec, dict):
        return ParsedOmcOutput(success=False, error_notices=error_notices)

    timings: dict[str, float] = {}
    for om_key, our_key in _TIMING_KEYS.items():
        if om_key in rec:
            try:
                timings[our_key] = float(rec[om_key])
            except (TypeError, ValueError):
                pass

    result_file = str(rec.get("resultFile") or "")
    messages = str(rec.get("messages") or "")
    success = bool(result_file) and "Failed" not in messages
    return ParsedOmcOutput(
        success=success,
        result_file=result_file,
        messages=messages,
        timings=timings or None,
        error_notices=error_notices,
    )


def _synthesize_stdout_artifact(
    worker_id: int,
    simulate_expr: str,
    returned_record: dict | None,
    error_string: str,
) -> str:
    """Render a text document equivalent to ``omc_stdout.txt`` so the report's
    artifact list matches the batch-backend shape. Diagnostic only — nothing
    downstream parses it.
    """
    parts = [
        f"// PersistentOpenModelicaBackend worker={worker_id}",
        f"// {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
        "",
        ">>> " + simulate_expr,
    ]
    if returned_record is not None:
        parts.append("record SimulationResult")
        for k, v in returned_record.items():
            if isinstance(v, str):
                parts.append(f'    {k} = "{v}",')
            else:
                parts.append(f"    {k} = {v},")
        parts.append("end SimulationResult;")
    else:
        parts.append("(no record returned)")
    if error_string:
        parts.append("")
        parts.append(">>> getErrorString()")
        parts.append(error_string)
    return "\n".join(parts) + "\n"


def _resolve_session_pid(session) -> int | None:
    """The omc PID behind *session*, or ``None`` if it can't be resolved.

    This PID is the watchdog's only kill target: without it a timeout
    leaves omc (and the compiled model binary under it) running for the
    rest of the machine's day. Two probes, because the session's API is
    version-dependent — OMPython used to expose ``getpid()`` and the
    build in use here does not, keeping its ``subprocess.Popen`` at
    ``omc_process._omc_process`` instead. Both are private-ish, so every
    step is guarded and the caller warns when both come up empty.
    """
    getpid = getattr(session, "getpid", None)
    if callable(getpid):
        try:
            pid = getpid()
        except Exception:  # pragma: no cover — best-effort probe
            pid = None
        if pid:
            return int(pid)
    proc = getattr(getattr(session, "omc_process", None), "_omc_process", None)
    pid = getattr(proc, "pid", None)
    if pid:
        return int(pid)
    return None


#: What omc writes to its log as it comes up, on every start — the last
#: lines of the log of an omc that is merely busy, and no news about it.
_OMC_STARTUP_LINES = ("Created ZeroMQ Server", "Dumped server port in file")


def _last_log_line(log: str) -> str:
    """The last line of omc's log that says something about omc: its
    startup notices are skipped. ``""`` when there is none."""
    for line in reversed(log.splitlines()):
        line = line.strip()
        if line and not line.startswith(_OMC_STARTUP_LINES):
            return line[-300:]
    return ""


def _is_notice_line(line: str) -> bool:
    lower = line.lower()
    return (
        lower.startswith("error")
        or lower.startswith("warning")
        or lower.startswith("notification")
    )


class OpenModelicaWorker(Worker):
    """One live OMCSessionZMQ process wrapped as a test worker."""

    kill_name_hint = "omc"
    exec_thread_prefix = "om"

    def __init__(
        self,
        worker_id: int,
        config: Config,
        om_config: OpenModelicaConfig,
        session_cls,
    ):
        super().__init__(worker_id, config)
        self.om_config = om_config
        self._OMCSession = session_cls
        self.session = None  # the OMCSessionZMQ instance
        self._n_tests_run = 0
        #: Seconds the last whole ``loadFile(package.mo)`` of the library
        #: took in this omc: what :meth:`reload` weighs a file-by-file
        #: load against.
        self._library_load_seconds = 0.0

    def start(self) -> None:
        """Launch omc via OMCSessionZMQ and apply the load/setup sequence.

        Mirrors the ``.mos`` preamble that :func:`build_simulate_mos` emits:
        ``setCommandLineOptions`` → dep loads (MSL auto-injected) →
        library load → user setup commands.
        """
        # review 2026-07-06 (finding 26): OMCSessionZMQ() resolves omc via
        # OPENMODELICAHOME / PATH and ignores the configured omc path that
        # batch mode honors — silently running a different omc version
        # against the same baseline partition. OMPython exposes no omc-path
        # argument, so when a specific path IS configured we derive
        # OPENMODELICAHOME from it (<home>/bin/omc → <home>) before the
        # session spawns. Defensive: only acts on an explicit, existing path.
        configured = getattr(self.config, "backend_path", None)
        if configured:
            omc_path = Path(configured).expanduser()
            if omc_path.exists():
                om_home = omc_path.parent.parent
                if os.environ.get("OPENMODELICAHOME") != str(om_home):
                    os.environ["OPENMODELICAHOME"] = str(om_home)
                    logger.warning(
                        "Persistent OpenModelica resolves omc via "
                        "OPENMODELICAHOME; derived OPENMODELICAHOME=%s from "
                        "the configured omc path %s",
                        om_home,
                        omc_path,
                    )
        self._step = "its launch"
        self.session = self._OMCSession()
        pid = _resolve_session_pid(self.session)
        if pid is None:
            logger.warning(
                "Worker %s: could not resolve the omc PID — the timeout "
                "watchdog has no kill target, so a hung omc will survive "
                "the run. Report the OMPython version; the session exposes "
                "neither getpid() nor a reachable Popen handle.",
                self.worker_id,
            )
        # OMPython launches omc and waits for its port file inside one call
        # (well under a second on a healthy install, 10 s at its own bound),
        # so this is the first moment the PID can be read.
        self._track_launch(pid)

        self._exec(f'setCommandLineOptions("--std={self.om_config.std_version}")')

        # Auto-inject MSL if the user didn't list it — matches the batch backend.
        deps = list(self.config.dependencies)
        if "Modelica" not in deps and not any(
            str(d).rstrip("/\\").endswith("Modelica") for d in deps
        ):
            deps = ["Modelica"] + deps

        for dep in deps:
            kind, arg = classify_dependency(dep)
            if kind == "loadModel":
                ok = self._exec(f"loadModel({arg})")
            else:
                ok = self._exec(f'loadFile("{arg}")')
            if not ok:
                err = _raw_get_error_string(self.session)
                raise RuntimeError(
                    f"Worker {self.worker_id}: failed to load dependency "
                    f"{dep!r} — {err.strip()[:400]}"
                )

        main_pkg = self.config.library_dir / "package.mo"
        main_pkg_fwd = str(main_pkg.resolve()).replace("\\", "/")
        began = time.monotonic()
        ok = self._exec(f'loadFile("{main_pkg_fwd}")')
        self._library_load_seconds = time.monotonic() - began
        if not ok:
            err = _raw_get_error_string(self.session)
            raise RuntimeError(
                f"Worker {self.worker_id}: failed to load library {main_pkg} "
                f"— {err.strip()[:400]}"
            )

        # User setup commands — best-effort; warn on failure but don't abort.
        for cmd in self.om_config.backend_setup or ():
            c = cmd.strip().rstrip(";")
            if not c:
                continue
            try:
                self.session.sendExpression(c)
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "Worker %s: setup command %r raised: %s",
                    self.worker_id,
                    cmd,
                    exc,
                )
        self._step = ""

    def _exec(self, expr: str) -> bool:
        """Send an expression that returns bool (loadModel / loadFile / setOption).

        Uses :func:`_raw_exec_bool` so the response bypasses OMPython's
        non-thread-safe pyparsing grammar. OMPython waits for the reply
        with no bound, so an omc that died or stopped answering parks this
        call for good; ``start_within`` is what gives up on it, naming the
        expression recorded here.
        """
        assert self.session is not None
        self._step = expr
        return _raw_exec_bool(self.session, expr)

    #: Seconds one file of the library costs to read again in an omc that
    #: holds the library: ``parseFile`` (0.01 measured), ``deleteClass``
    #: (0.08 to 0.13) and ``loadFile`` of the file (0.07 to 0.25), on a
    #: native file system and on /mnt/d alike. The whole library again is
    #: what its first load was — 1 s for a 3 270-file library on the first,
    #: 92 s on the second — which is what :meth:`reload` weighs this with.
    _FILE_LOAD_SECONDS = 0.4

    def reload(self, changes: LibraryChanges | None) -> None:
        """``loadFile`` again, in the live omc: each changed file of the
        library by itself where that is enough and the cheaper way, else
        its ``package.mo`` — the whole library; a dependency given as a
        path that holds a changed file is loaded whole, and the classes
        that must exist after it are the ones its file names.

        omc KEEPS the classes it holds when a load fails — a run would
        then pass on the old model — so every failure here raises
        :class:`LibraryReloadError` with omc's message, and the caller
        fails the test. A failure after some files were loaded leaves omc
        holding a mix (a new class of one file, the old of another, none
        of a third): the caller asks the next reload for everything
        (``changes`` None), which is the whole library and replaces it all.

        The whole library is what replaces everything: a deleted file's
        class is gone after it, and a file whose class does not carry the
        file's name is refused. It is used when a file was added or
        removed, when a ``package.mo`` or a ``package.order`` changed (a
        ``package.mo`` is not a file omc loads by itself), when nothing is
        known of what changed, and when the changed files are too many
        for one-by-one to be cheaper. One file is read as strictly as
        that (:meth:`_load_file_again`)."""
        assert self.session is not None
        config = self.config
        touched = (
            None
            if changes is None
            else (*changes.changed, *changes.added, *changes.removed)
        )
        for dep in config.dependencies:
            root = dependency_root(dep)
            if root is None:
                continue  # by name: omc found it, and nothing swept it
            if touched is None or any(under(path, root) for path in touched):
                _, dep_file = classify_dependency(dep)
                # Its classes are named by its file, never by its
                # directory: `Lib 1.0.0`, a symbolic link of any name.
                names, error = self._classes_in(dep_file)
                if error is None and not names:
                    error = "it holds no class"
                if error is not None:
                    raise LibraryReloadError(f"{root.name} does not load: {error}")
                self._load_whole(dep_file, *names)
        files = self._files_to_load_again(changes)
        if files is None:
            began = time.monotonic()
            main_pkg = str((config.library_dir / "package.mo").resolve())
            self._load_whole(main_pkg.replace("\\", "/"), str(config.library_name))
            self._library_load_seconds = time.monotonic() - began
            return
        for path in files:
            self._load_file_again(path)

    def _files_to_load_again(self, changes: LibraryChanges | None) -> list[Path] | None:
        """The library's own files to read one by one for *changes*, in
        order; ``None`` when the whole library is to be loaded."""
        if changes is None:
            return None
        library_dir = self.config.library_dir
        added_or_removed = [
            path
            for path in (*changes.added, *changes.removed)
            if under(path, library_dir)
        ]
        changed = {path for path in changes.changed if under(path, library_dir)}
        if added_or_removed or any(
            path.name in ("package.mo", "package.order") for path in changed
        ):
            return None
        if len(changed) * self._FILE_LOAD_SECONDS > self._library_load_seconds:
            return None
        return sorted(changed)

    def _classes_in(self, file: str) -> tuple[list[str], str | None]:
        """The top-level classes *file* holds, as ``parseFile`` names them
        — which loads nothing — or omc's message for a file it cannot
        parse."""
        self._step = f'parseFile("{file}")'
        holds, error = _raw_reply(self.session, self._step)
        if error is not None:
            return [], error
        return [n.strip() for n in holds.strip("{}").split(",") if n.strip()], None

    def _load_whole(self, package_file: str, *names: str) -> None:
        """``loadFile`` a library's file; each of *names* must be a class
        after it (a load that fails leaves the old ones, and says so)."""
        self._step = f'loadFile("{package_file}")'
        _, error = _raw_reply(self.session, self._step)
        if error is None:
            missing = [
                name
                for name in names
                if not _raw_exec_bool(self.session, f"existClass({name})")
            ]
            if missing:
                error = f"loadFile left no class {', '.join(missing)}"
        if error is not None:
            raise LibraryReloadError(f"{', '.join(names)} does not load: {error}")

    def _load_file_again(self, path: Path) -> None:
        """Read one changed file of the library again, as strictly as the
        whole library's load reads it: the file must hold exactly the
        class its path names (``parseFile`` says what it holds, without
        loading it), that class is deleted — so it cannot outlive a file
        that fails to load — the file is loaded, and the class must then
        exist. Measured against the library's whole load on a model file,
        a component's and a base class's (the models using it saw the new
        text), and on a syntax error, a renamed class, a wrong ``within``,
        a second class in the file and an empty file: refused each time
        the whole load refuses."""
        session, config = self.session, self.config
        library = str(config.library_name)
        relative = path.relative_to(config.library_dir)
        name = ".".join((library, *relative.with_suffix("").parts))
        file = str(path.resolve()).replace("\\", "/")

        def refuse(why: str) -> LibraryReloadError:
            return LibraryReloadError(
                f"{library} does not load: {relative.as_posix()}: {why}"
            )

        names, error = self._classes_in(file)
        if error is not None:
            # The class omc holds is the old file's and stays, and the
            # caller fails the test.
            raise refuse(error)
        if names != [name]:
            raise refuse(f"it holds {', '.join(names) or 'no class'}, not {name}")
        self._step = f"deleteClass({name})"
        # false for a class omc does not hold: nothing to delete.
        _raw_exec_bool(session, self._step)
        self._step = f'loadFile("{file}")'
        _, error = _raw_reply(session, self._step)
        if error is None and not _raw_exec_bool(session, f"existClass({name})"):
            error = f"loadFile left no class {name}"
        if error is not None:
            raise refuse(error)

    def process_detail(self) -> str:
        omc = getattr(self.session, "omc_process", None)
        proc = getattr(omc, "_omc_process", None)
        if proc is None:
            return ""
        code = proc.poll()
        state = "omc is still running" if code is None else f"omc exited (code {code})"
        if self._step:
            state += f" during {self._step}"
        try:
            log = _last_log_line(omc.get_log() or "")
        except Exception:
            log = ""
        return state + (f"; omc log: {log}" if log else "")

    def cancel_running(self, test_key: str) -> bool:
        """Kill what omc spawned for the test — make and the compiler during
        the build, the compiled model during the run — and leave omc itself:
        its ``simulate()`` returns a failed record at once.

        ``False`` while omc has no child (its own frontend and backend
        phases), which only killing omc ends; the watchdog does that, as it
        does when omc has not come back within ``cancel_grace``.
        """
        killed = False
        for child in live_children(self.pids, name_hint=self.kill_name_hint):
            try:
                child.kill()
                killed = True
            except Exception:
                continue  # already gone
        return killed

    def tool_version(self) -> str | None:
        """Actual omc version via ``getVersion()`` (wire format is a quoted
        string, same as an error string). Best-effort → ``None`` on failure."""
        if self.session is None:
            return None
        try:
            raw = self.session.sendExpression("getVersion()", parsed=False)
        except Exception:
            return None
        s = (raw or "").strip()
        if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
            s = s[1:-1]
        s = s.replace('\\"', '"').replace("\\\\", "\\").strip()
        return s or None

    def library_versions(self) -> dict:
        """Loaded Modelica Standard Library version via ``getVersion(Modelica)``.

        Ensures MSL is loaded first (``loadModel(Modelica)`` is idempotent) so
        the probe is robust even before any test has pulled it in. Wire format
        is a quoted string. Best-effort → ``{}`` on failure."""
        if self.session is None:
            return {}
        try:
            self.session.sendExpression("loadModel(Modelica)", parsed=False)
            raw = self.session.sendExpression("getVersion(Modelica)", parsed=False)
        except Exception:
            return {}
        s = (raw or "").strip()
        if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
            s = s[1:-1]
        s = s.strip()
        return {"Modelica": s} if s else {}

    def run_test(
        self,
        test: TestModel,
        test_key: str,
        progress=None,
    ) -> TestRunResult:
        """Run one test in this worker. Returns a :class:`TestRunResult`.

        Emits ``translating`` / ``simulating`` / ``finalizing`` phase events
        around the single OMPython ``simulate()`` call. The record returned
        by OMPython is a :class:`dict` — no stdout regex parsing needed;
        wall-time splits come from the record's ``timeFrontend`` / etc.
        """
        test_dir = self.config.work_dir / test_key
        # Defensive duplicate of cmd_run's central wipe (D91); kept here so a
        # direct caller of run_tests outside cmd_run still gets clean state.
        if test_dir.exists():
            shutil.rmtree(test_dir)
        test_dir.mkdir(parents=True, exist_ok=True)

        # The variable filter (the UnitTests component's names included) is
        # build_simulate_args' own business — same call as the batch backend.
        sim_args = build_simulate_args(
            test,
            self.om_config.diagnostic_variables,
            mat_sync=self.om_config.mat_sync,
        )
        simulate_expr = f"simulate({test.model_id}, {', '.join(sim_args)})"

        # Bind the session once. run_test executes on a thread the watchdog
        # abandons on timeout (`t.join(0.5)`); the pool then restarts this
        # worker and `self.session` becomes a *new* OMCSessionZMQ. An
        # abandoned thread that later unblocks must keep talking to the dead
        # session — re-reading the attribute would let it drive the fresh one
        # concurrently with the next test.
        session = self.session
        start = time.monotonic()
        translation_wall: float | None = None
        sim_wall: float | None = None
        record_dict: dict | None = None
        try:
            test_dir_fwd = str(test_dir).replace("\\", "/")
            # cd() returns the new cwd as a quoted string — raw mode is fine.
            session.sendExpression(f'cd("{test_dir_fwd}")', parsed=False)

            if progress is not None:
                progress.on_phase(test_key, "translating")

            self._step = f"simulate({test.model_id})"
            record_dict, _raw_simulate_response = _raw_send_simulate(
                session,
                simulate_expr,
            )
        except Exception as exc:
            elapsed = time.monotonic() - start
            self._n_tests_run += 1
            # Disk-fallback: OM may have written result_res.mat before failing.
            disk_result = self._evaluate_from_disk(test, test_key, elapsed)
            if disk_result.success:
                return disk_result
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=elapsed,
                error_message=f"OMCSessionZMQ error: {type(exc).__name__}: {exc}",
                translation_wall=translation_wall,
                sim_wall=sim_wall,
            )

        error_string = _raw_get_error_string(session)
        error_notices = [
            line.strip()
            for line in error_string.splitlines()
            if line.strip() and _is_notice_line(line.strip())
        ]
        parsed = _parsed_from_record(record_dict, error_notices)

        if parsed.timings is not None:
            t = parsed.timings
            translation_wall = (
                t.get("frontend", 0.0)
                + t.get("backend", 0.0)
                + t.get("simcode", 0.0)
                + t.get("templates", 0.0)
                + t.get("compile", 0.0)
            )
            sim_wall = t.get("simulation")

        if progress is not None:
            progress.on_phase(test_key, "simulating")

        try:
            (test_dir / OpenModelicaBackend.STDOUT_FILENAME).write_text(
                _synthesize_stdout_artifact(
                    self.worker_id,
                    simulate_expr,
                    record_dict,
                    error_string,
                ),
                encoding="utf-8",
            )
        except OSError as exc:  # pragma: no cover
            logger.debug(
                "Worker %s: stdout artifact write failed: %s",
                self.worker_id,
                exc,
            )

        if progress is not None:
            progress.on_phase(test_key, "finalizing")

        self._n_tests_run += 1
        elapsed = time.monotonic() - start

        mat_path = test_dir / OpenModelicaBackend.RESULT_MAT_FILENAME
        stats: dict = {}
        if parsed.timings is not None:
            stats["timing"] = dict(parsed.timings)

        if parsed.success and mat_path.exists():
            # Belt on the accept-integrity invariant (D93 precedent / D99):
            # even a success-reporting simulate() record must show a
            # trajectory that reached stop_time — a partial .mat blessed
            # by --accept poisons the baseline. Same rule + tolerance as
            # the disk rescue below, so the two paths can't disagree.
            reached, last_time = mat_reaches_time(mat_path, float(test.stop_time))
            if reached:
                return TestRunResult(
                    model_id=test.model_id,
                    test_key=test_key,
                    success=True,
                    elapsed=elapsed,
                    translation_wall=translation_wall,
                    sim_wall=sim_wall,
                    statistics=stats or None,
                )
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=elapsed,
                error_message=(
                    f"simulate() reported success but result_res.mat reaches "
                    f"t={last_time!r} of stop_time={test.stop_time} — "
                    f"partial result refused"
                ),
            )

        # Disk-based cross-check — record may have misread success.
        disk_ok = self._evaluate_from_disk(test, test_key, elapsed)
        if disk_ok.success:
            disk_ok.translation_wall = translation_wall
            disk_ok.sim_wall = sim_wall
            disk_ok.statistics = stats or None
            return disk_ok

        err_parts: list[str] = []
        if error_notices:
            err_parts.append("; ".join(error_notices[:5]))
        if parsed.messages:
            err_parts.append(parsed.messages.strip())
        if not parsed.result_file:
            err_parts.append("no result file produced")
        msg = " | ".join(p for p in err_parts if p)[:2048] or "omc simulation failed"

        if any("Failed to load package Modelica" in n for n in error_notices):
            msg = (
                "MSL not installed. Run: omc -e "
                "'updatePackageIndex(); installPackage(Modelica); "
                "getErrorString();' | " + msg
            )

        return TestRunResult(
            model_id=test.model_id,
            test_key=test_key,
            success=False,
            elapsed=elapsed,
            error_message=msg,
            translation_wall=translation_wall,
            sim_wall=sim_wall,
            statistics=stats or None,
        )

    def _evaluate_from_disk(
        self,
        test: TestModel,
        test_key: str,
        elapsed: float,
    ) -> TestRunResult:
        """Inspect ``result_res.mat`` on disk to produce a TestRunResult.

        Used as fallback when the in-process ``simulate()`` call couldn't
        finish cleanly (timeout / worker exception). OM doesn't have
        Dymola's ``dsfinal.txt`` completion marker; we use MAT time extents
        only.
        """
        test_dir = self.config.work_dir / test_key
        mat_path = test_dir / OpenModelicaBackend.RESULT_MAT_FILENAME
        if not mat_path.exists():
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=elapsed,
                error_message="No result file produced",
            )
        reached, last_time = mat_reaches_time(mat_path, float(test.stop_time))
        if last_time is None:
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=elapsed,
                error_message="result_res.mat unreadable",
            )
        if reached:
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=True,
                elapsed=elapsed,
            )
        return TestRunResult(
            model_id=test.model_id,
            test_key=test_key,
            success=False,
            elapsed=elapsed,
            error_message=(
                f"Stopped early at T={last_time:.6g} of {float(test.stop_time):.6g}"
            ),
        )

    def _detach_interface(self) -> object | None:
        s = self.session
        self.session = None
        return s

    def _shutdown_interface(self, handle: object, grace: float) -> None:
        try:
            handle.sendExpression("quit()")  # type: ignore[attr-defined]
        except Exception as exc:
            if type(exc).__name__ != "ZMQError":
                raise
            # Expected on the timeout path: the abandoned exec thread left a
            # simulate() in flight, so the REQ socket is still awaiting its
            # reply and refuses the send with EFSM. The PID kill is the real
            # teardown here; let the template's warning stay for genuinely
            # unexpected failures. The socket is deliberately NOT closed —
            # a zmq socket must not be closed under a thread parked in
            # recv() on it, which is exactly where the exec thread is.
            logger.debug(
                "Worker %s: omc refused the graceful quit() (%s)",
                self.worker_id,
                exc,
            )

    def is_alive(self) -> bool:
        return self.session is not None and not self.tool_gone()


# ---------------------------------------------------------------------------


class PersistentOpenModelicaBackend(PersistentBackendBase, OpenModelicaBackend):
    """OpenModelica backend using persistent OMCSessionZMQ workers + a queue.

    Inherits the dispatch-loop machinery from :class:`PersistentBackendBase`
    and the batch :class:`OpenModelicaBackend` for ``read_result`` + config
    extraction. Order matters — ``PersistentBackendBase`` first so its
    ``run_tests`` template wins over the batch backend's per-test override.
    """

    worker_cls = OpenModelicaWorker
    backend_label = "OpenModelica"

    #: CANCEL is the worker's: ``OpenModelicaWorker.cancel_running`` (omc's
    #: children) with the watchdog's kill behind it. The batch class, one
    #: ``omc`` subprocess per test, has no such stop.
    capabilities = OpenModelicaBackend.capabilities | {Capability.CANCEL}

    @classmethod
    def preflight(cls, config) -> None:
        # Probes OMPython availability (the optional `[om]` extra). Cheap
        # — load_omc_session caches after first call. RuntimeError carries
        # the install hint that the CLI surfaces verbatim.
        # Lazy import inside the method so tests can monkeypatch
        # ``session_loader.load_omc_session`` and have the patch take
        # effect here without also having to re-bind this module's
        # already-imported reference.
        from .session_loader import load_omc_session as _load

        _load()

    def setup_before_workers(self) -> None:
        # Cache the OMCSessionZMQ class for make_worker. Called once per run;
        # load_omc_session is itself cached after the first call.
        self._session_cls = load_omc_session()

    def make_worker(self, worker_id: int) -> OpenModelicaWorker:
        return OpenModelicaWorker(
            worker_id,
            self.config,
            self.om_config,
            self._session_cls,
        )

    def _probe_worker_version(self, live_workers: list) -> str | None:
        for w in live_workers:
            v = w.tool_version()
            if v:
                return v
        return None

    def _probe_library_versions(self, live_workers: list) -> dict | None:
        for w in live_workers:
            libs = w.library_versions()
            if libs:
                return libs
        return None
