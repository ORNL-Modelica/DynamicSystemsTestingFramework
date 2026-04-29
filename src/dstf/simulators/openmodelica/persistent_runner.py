"""Persistent-worker OpenModelica runner using OMPython (OMCSessionZMQ).

Unlike the default batched runner (which spawns one ``omc`` subprocess per
test driven by a generated ``simulate.mos``), this runner keeps N long-lived
``OMCSessionZMQ`` processes alive for the whole run. Each worker loads MSL
+ the library once and then pulls tests off a shared queue.

Benefits:
  - MSL + library load paid once per worker (major win for TRANSFORM —
    per-test compile was ~50–100s in the 2026-04-22 sweep; amortizing
    library load gives a 5–10× wall-time cut)
  - Per-test granularity: start/finish events fire as each test transitions
  - Natural work-stealing: whichever worker finishes first grabs the next test
  - Per-test timeout isolation: a hung test kills one worker, not the batch

The default CLI mode selects this runner; pass ``--batch`` to fall back to
the subprocess-per-test runner, which doesn't require OMPython. Persistent
mode also falls back to batch automatically if OMPython can't be imported
(mirrors the Dymola Python-interface fallback).

Phase labels are cosmetic — OM's ``simulate()`` bundles build + run in one
call with no mid-call progress hook, so we emit ``translating`` before the
call and ``simulating`` after (matching the existing batch runner pattern).
Per-phase wall timings are still accurate in the final report because they
come from the returned ``SimulationResult`` record.
"""

from __future__ import annotations

import logging
import queue
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from ...config import Config
from ...discovery.test_registry import TestModel
from ..base import (
    BatchManifest,
    PersistentRunnerBase,
    TestRunResult,
    Worker,
    _print_progress,
    assign_test_keys,
)
from ..common.mat_reader import read_mat_time_extents
from .log_parser import ParsedOmcOutput, _TIMING_KEYS, parse_omc_stdout
from .mos_generator import build_simulate_args, classify_dependency
from .runner import OpenModelicaConfig, OpenModelicaRunner
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


def _raw_send_simulate(session, expr: str) -> tuple[Optional[dict], str]:
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
    rec: Optional[dict],
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
    returned_record: Optional[dict],
    error_string: str,
) -> str:
    """Render a text document equivalent to ``omc_stdout.txt`` so the report's
    artifact list matches the batch-runner shape. Diagnostic only — nothing
    downstream parses it.
    """
    parts = [
        f"// PersistentOpenModelicaRunner worker={worker_id}",
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


def _is_notice_line(line: str) -> bool:
    lower = line.lower()
    return (
        lower.startswith("error")
        or lower.startswith("warning")
        or lower.startswith("notification")
    )


class OpenModelicaWorker(Worker):
    """One live OMCSessionZMQ process wrapped as a test runner."""

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
        self.pids: set[int] = set()
        self._n_tests_run = 0
        self._n_restarts = 0

    def start(self) -> None:
        """Launch omc via OMCSessionZMQ and apply the load/setup sequence.

        Mirrors the ``.mos`` preamble that :func:`build_simulate_mos` emits:
        ``setCommandLineOptions`` → dep loads (MSL auto-injected) →
        library load → user setup commands.
        """
        self.session = self._OMCSession()
        try:
            pid = self.session.getpid()
            if pid:
                self.pids = {int(pid)}
        except Exception:  # pragma: no cover — best-effort
            self.pids = set()

        self._exec(f'setCommandLineOptions("--std={self.om_config.std_version}")')

        # Auto-inject MSL if the user didn't list it — matches batch runner.
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
                err = self._get_error_string()
                raise RuntimeError(
                    f"Worker {self.worker_id}: failed to load dependency "
                    f"{dep!r} — {err.strip()[:400]}"
                )

        main_pkg = self.config.library_dir / "package.mo"
        main_pkg_fwd = str(main_pkg.resolve()).replace("\\", "/")
        ok = self._exec(f'loadFile("{main_pkg_fwd}")')
        if not ok:
            err = self._get_error_string()
            raise RuntimeError(
                f"Worker {self.worker_id}: failed to load library {main_pkg} "
                f"— {err.strip()[:400]}"
            )

        # User setup commands — best-effort; warn on failure but don't abort.
        for cmd in self.om_config.simulator_setup or ():
            c = cmd.strip().rstrip(";")
            if not c:
                continue
            try:
                self.session.sendExpression(c)
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "Worker %s: setup command %r raised: %s",
                    self.worker_id, cmd, exc,
                )

    def _exec(self, expr: str) -> bool:
        """Send an expression that returns bool (loadModel / loadFile / setOption).

        Uses :func:`_raw_exec_bool` so the response bypasses OMPython's
        non-thread-safe pyparsing grammar.
        """
        assert self.session is not None
        return _raw_exec_bool(self.session, expr)

    def _get_error_string(self) -> str:
        assert self.session is not None
        return _raw_get_error_string(self.session)

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
        if test_dir.exists():
            shutil.rmtree(test_dir)
        test_dir.mkdir(parents=True, exist_ok=True)

        # UnitTests component auto-names (same convention as batch runner).
        extra_filter_names: list[str] = []
        if test.source in ("unit_tests", "both") and test.n_vars > 0:
            extra_filter_names = [
                f"unitTests.x[{i}]" for i in range(1, test.n_vars + 1)
            ]

        sim_args = build_simulate_args(
            test,
            self.om_config.diagnostic_variables,
            extra_filter_names=extra_filter_names,
        )
        simulate_expr = f"simulate({test.model_id}, {', '.join(sim_args)})"

        start = time.monotonic()
        translation_wall: Optional[float] = None
        sim_wall: Optional[float] = None
        record_dict: Optional[dict] = None
        try:
            test_dir_fwd = str(test_dir).replace("\\", "/")
            # cd() returns the new cwd as a quoted string — raw mode is fine.
            self.session.sendExpression(f'cd("{test_dir_fwd}")', parsed=False)

            if progress is not None:
                progress.on_phase(test_key, "translating")

            record_dict, _raw_simulate_response = _raw_send_simulate(
                self.session, simulate_expr,
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

        error_string = self._get_error_string()
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
            (test_dir / OpenModelicaRunner.STDOUT_FILENAME).write_text(
                _synthesize_stdout_artifact(
                    self.worker_id, simulate_expr, record_dict, error_string,
                ),
                encoding="utf-8",
            )
        except OSError as exc:  # pragma: no cover
            logger.debug(
                "Worker %s: stdout artifact write failed: %s", self.worker_id, exc,
            )

        if progress is not None:
            progress.on_phase(test_key, "finalizing")

        self._n_tests_run += 1
        elapsed = time.monotonic() - start

        mat_path = test_dir / OpenModelicaRunner.RESULT_MAT_FILENAME
        stats: dict = {}
        if parsed.timings is not None:
            stats["timing"] = dict(parsed.timings)

        if parsed.success and mat_path.exists():
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=True,
                elapsed=elapsed,
                translation_wall=translation_wall,
                sim_wall=sim_wall,
                statistics=stats or None,
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
        mat_path = test_dir / OpenModelicaRunner.RESULT_MAT_FILENAME
        if not mat_path.exists():
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=elapsed,
                error_message="No result file produced",
            )
        extents = read_mat_time_extents(mat_path)
        if extents is None:
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=elapsed,
                error_message="result_res.mat unreadable",
            )
        last_time = extents[1]
        stop_time = float(test.stop_time)
        tol = max(1e-6, abs(stop_time) * 1e-6)
        if last_time + tol >= stop_time:
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
            error_message=f"Stopped early at T={last_time:.6g} of {stop_time:.6g}",
        )

    def close(self, grace: float = 5.0) -> None:
        """Terminate the omc subprocess. Graceful ``quit()`` first, then
        psutil hard-kill of tracked PIDs if that hangs. Idempotent.
        """
        s = self.session
        self.session = None
        if s is not None:
            done = threading.Event()

            def _quit():
                try:
                    s.sendExpression("quit()")
                except Exception:
                    pass
                finally:
                    done.set()

            t = threading.Thread(target=_quit, daemon=True)
            t.start()
            done.wait(grace)
        self._kill_tracked_pids()

    def _kill_tracked_pids(self) -> None:
        if not self.pids:
            return
        import psutil
        for pid in list(self.pids):
            try:
                p = psutil.Process(pid)
                if "omc" in (p.name() or "").lower():
                    p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        self.pids = set()

    def is_alive(self) -> bool:
        return self.session is not None

    def run_test_with_timeout(
        self,
        test: TestModel,
        test_key: str,
        timeout: float,
        progress=None,
    ) -> TestRunResult:
        """Run a test with a watchdog. On timeout, hard-kill omc. After a
        timeout or worker-level exception, :meth:`is_alive` is False and
        the worker must be restarted before another test is dispatched.
        """
        result_box: list[Optional[TestRunResult]] = [None]
        exc_box: list[Optional[BaseException]] = [None]
        start_ts = time.monotonic()

        def _runner():
            try:
                result_box[0] = self.run_test(test, test_key, progress=progress)
            except BaseException as e:
                exc_box[0] = e

        t = threading.Thread(
            target=_runner, daemon=True, name=f"om-exec-{self.worker_id}",
        )
        t.start()
        t.join(timeout)

        if t.is_alive():
            self.close(grace=1.0)
            t.join(0.5)
            elapsed = time.monotonic() - start_ts
            disk_result = self._evaluate_from_disk(test, test_key, elapsed)
            if disk_result.success:
                return disk_result
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=elapsed,
                error_message=f"Timed out after {timeout:.0f}s",
                timed_out=True,
            )

        if exc_box[0] is not None:
            elapsed = time.monotonic() - start_ts
            disk_result = self._evaluate_from_disk(test, test_key, elapsed)
            if disk_result.success:
                self.close(grace=1.0)
                return disk_result
            self.close(grace=1.0)
            exc = exc_box[0]
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=elapsed,
                error_message=f"Worker exception: {type(exc).__name__}: {exc}",
            )

        return result_box[0]  # type: ignore[return-value]


# ---------------------------------------------------------------------------


class PersistentOpenModelicaRunner(PersistentRunnerBase, OpenModelicaRunner):
    """OpenModelica runner using persistent OMCSessionZMQ workers + a queue.

    Inherits the dispatch-loop machinery from :class:`PersistentRunnerBase`
    and the batch :class:`OpenModelicaRunner` for ``read_result`` + config
    extraction. Order matters — ``PersistentRunnerBase`` first so its
    ``run_tests`` template wins over the batch runner's per-test override.
    """

    worker_cls = OpenModelicaWorker
    backend_label = "OpenModelica"

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
            worker_id, self.config, self.om_config, self._session_cls,
        )
