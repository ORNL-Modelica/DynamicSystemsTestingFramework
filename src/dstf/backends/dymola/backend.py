"""Dymola-specific backend with batch execution."""

import logging
import math
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...config import Config
from ...config_shape import FILE, Setting, default
from ...discovery.library_state import dependency_file
from ...discovery.test_registry import TestModel
from ...storage.batch_manifest import BatchManifest
from .. import register
from ..base import Backend, Diagnosis, _print_run_header
from ..common import result_files
from ..common.mat_reader import read_result_mat
from ..common.proc_kill import kill_process_tree
from ..common.result_variables import (
    compute_needed_variables,
    extract_variables,
    fold_diagnostics_into_statistics,
    unfinished_result,
)
from ..locate import Found, Located, find_executable
from ..types import (
    Capability,
    DatasetType,
    TestResult,
    TestRunResult,
    timeout_message,
)
from . import interface_loader
from .interface_loader import INTERFACE, INTERFACE_VARIABLE
from .log_parser import parse_test_logs

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DymolaConfig:
    """Dymola-specific settings extracted from the universal Config."""

    # Each default is its key's, read from the table it is written in:
    # this backend's `settings`, and testing.json's.
    show_ide: bool = field(
        default_factory=lambda: default("show_ide", DymolaBackend.settings)
    )
    #: Tests per Dymola session on the batch path; None: every test of a
    #: worker in one session.
    batch_size: int | None = field(
        default_factory=lambda: default("batch_size", DymolaBackend.settings)
    )
    backend_setup: list[str] = field(default_factory=lambda: default("backend_setup"))
    diagnostic_variables: list[str] = field(
        default_factory=lambda: default("diagnostic_variables")
    )

    @classmethod
    def from_config(cls, config: Config) -> "DymolaConfig":
        """Extract Dymola-specific fields from the universal Config: its
        block of ``testing.json`` (``DymolaBackend.settings``) and the keys
        every backend shares."""
        settings = DymolaBackend.settings_of(config)
        return cls(
            show_ide=settings["show_ide"],
            batch_size=settings["batch_size"],
            backend_setup=list(config.backend_setup),
            diagnostic_variables=list(config.diagnostic_variables),
        )


@register("Dymola")
class DymolaBackend(Backend):
    """Runs Modelica simulations using Dymola with batch execution.

    Instead of launching a separate Dymola process per test, tests are
    grouped into batches. Each batch runs in a single Dymola session:
    load libraries once, run N tests, exit. This dramatically reduces
    startup overhead for large test suites.
    """

    # NOTE: no Capability.FMU_EXPORT here. Dymola *can* export FMUs, but only
    # through the DymolaInterface — see PersistentDymolaBackend, which declares
    # the flag and implements export_fmu(). Batch mode would need a
    # `translateModelFMU(...)` .mos script + `dymola -nowindow` spawn + FMU
    # locator; nobody has needed it (persistent is the default path), so
    # rather than declare a capability we don't have, we don't declare it and
    # the cross-backend chain skips batch-mode Dymola with a clear message.
    capabilities = frozenset(
        {
            Capability.PERSISTENT_WORKERS,  # DymolaInterface-based workers (default)
            Capability.BATCH_FALLBACK,  # .mos script backend (--batch flag)
            Capability.LIVE_RESULTS,  # dymosim appends dsres.mat as it goes
        }
    )
    produced_datasets = frozenset({DatasetType.TIME_SERIES})
    settings = {
        **Backend.settings,
        "show_ide": Setting(
            {"type": "boolean"},
            False,
            "Show Dymola's window while it runs, instead of running it hidden.",
        ),
        "batch_size": Setting(
            {"type": "integer", "minimum": 1},
            None,
            "With `run --batch`: how many tests one Dymola session runs "
            "before it is closed and the next is started. Small values (3 "
            "to 5) spread the load across workers and lose fewer tests to "
            "a crash, but load the library more often. Default: every test "
            "of a worker in one session.",
        ),
        "interface": Setting(
            {"type": "string", "minLength": 1},
            None,
            "Dymola's Python interface: the archive (dymola-*.whl or "
            "dymola.egg) or the folder that holds it. A relative path is "
            "relative to this file, and ~ is your home directory. "
            "Default: the DYMOLA_INTERFACE_PATH environment variable, else "
            "Modelica/Library/python_interface of the install of the "
            "Dymola that was found, and no other's. Only where that "
            "install cannot be determined, that of the newest Dymola in "
            "the standard install folders — which `dstf check` and the run "
            "then say.",
            path=FILE,
        ),
    }
    source_types = frozenset({"modelica"})
    binaries = ("dymola",)
    artifact_files = (
        ("dslog.txt", "Simulation log"),
        ("translation_log.txt", "Translation log"),
        ("dsin.txt", "Simulation input"),
        ("dsfinal.txt", "Final values"),
        ("simulate.mos", "Simulation script"),
        ("dsres.mat", "Result file"),
    )

    def __init__(self, config: Config):
        super().__init__(config)
        self.dymola_config = DymolaConfig.from_config(config)

    @classmethod
    def persistent_backend_cls(cls):
        from .persistent_backend import PersistentDymolaBackend

        return PersistentDymolaBackend

    @classmethod
    def locate(cls, settings: Mapping[str, Any]) -> Located:
        """The Dymola executable, then its Python interface — looked for
        in THAT executable's install and no other, so a project that
        names an older Dymola is not driven through a newer one's
        interface (``interface_loader.find_interface``)."""
        executable = find_executable(
            settings,
            cls.binaries,
            folders=[
                (base, interface_loader.executable_glob())
                for base in interface_loader.platform_glob_bases()
            ],
            best_first=interface_loader.dymola_sort_key,
        )
        return Located(
            (executable, interface_loader.find_interface(settings, executable.path))
        )

    @classmethod
    def interface_of(cls, config: Config) -> Found:
        """The Python interface a run of ``config`` loads: the one its
        ``Config`` located, else — a config that located none — this
        backend's own search."""
        located = getattr(config, "located", None)
        if located is None or located.part(INTERFACE) is None:
            located = cls.locate(cls.settings_of(config))
        found = located.part(INTERFACE)
        assert found is not None  # `locate` always answers for it
        return found

    @classmethod
    def diagnose(
        cls, located: Located, *, entry: str = "", config: Config | None = None
    ) -> Diagnosis:
        """The located parts, then whether the interface that was found
        imports: the persistent workers drive Dymola through it."""
        found = located.part(INTERFACE) or Found(INTERFACE)
        info = interface_loader.describe_dymola_interface(found)
        lines = located.lines(entry)
        if info["format"]:
            lines.append(f"  interface format: .{info['format']}")
        if info["sys_path_entry"]:
            lines.append(f"  sys.path entry: {info['sys_path_entry']}")
        lines.append(f"  import ok: {info['import_ok']}")
        if info.get("dymola_interface_module"):
            lines.append(f"  module: {info['dymola_interface_module']}")
        if info["error"] and found.path is not None:
            lines.append(f"  error: {info['error']}")
        if info["discovered_dirs"]:
            lines.append("  interfaces in the standard folders (newest first):")
            lines.extend(f"    {d}" for d in info["discovered_dirs"])
        fixes: list[str] = []
        if not located.found:
            fixes.append(
                'Install Dymola, or set "executables" in the Dymola block of '
                'testing.json\'s "backends" to where it is'
            )
        if not info["import_ok"]:
            fixes.append(
                'Set "interface" in the Dymola block of testing.json\'s "backends", '
                f"or the environment variable {INTERFACE_VARIABLE}, to the "
                "archive or its folder (run --batch needs no interface)"
            )
        return Diagnosis(
            tuple(lines), located.found and bool(info["import_ok"]), tuple(fixes)
        )

    def run_tests(self, tests: list[TestModel]) -> list[BatchManifest]:
        """Run tests in batches with parallelism."""
        if not tests:
            return []

        setup = self._prepare_run(tests)
        total = setup.total
        manifest = setup.manifest
        test_items = setup.keyed_tests

        # Clean and recreate per-test directories ONLY for tests being run
        # this batch (preserves prior dirs for tests not in this run).
        # Note (D91): cmd_run.cli now centrally wipes in-scope test_dirs
        # before backend.run_tests; this in-backend wipe is a defensive
        # duplicate that protects against future callers of run_tests
        # outside cmd_run (e.g., direct test harness calls). Cheap (rmtree
        # on empty) and self-documenting at the point freshness matters.
        import shutil

        for test, test_key in test_items:
            test_dir = self.config.work_dir / test_key
            if test_dir.exists():
                shutil.rmtree(test_dir)
            test_dir.mkdir(parents=True, exist_ok=True)
            _generate_test_mos(test, test_key, test_dir)

        # Generate shared startup and shutdown scripts
        startup_path = _generate_startup_mos(self.config, self.dymola_config)
        shutdown_path = _generate_shutdown_mos(self.config)

        # Split tests into batches for parallel workers.
        # If batch_size is set, chunks are that size (many small batches, queue-dispatched
        # to workers — better load balancing, smaller crash blast radius).
        # Otherwise use one big batch per worker (original behavior, minimizes library reloads).
        n_workers = max(1, self.config.parallel)
        batch_size = self.dymola_config.batch_size or math.ceil(total / n_workers)
        batches = []
        for i in range(0, total, batch_size):
            batches.append(test_items[i : i + batch_size])

        _print_run_header(
            total,
            f"in {len(batches)} batch(es) of up to {batch_size}",
            n_workers,
            self.config.timeout,
            self.config.work_dir,
            timeout_per_test=True,
            metadata=setup.run_metadata,
            quiet=self.quiet,
        )

        # Generate and run batch scripts
        all_results: list[TestRunResult] = []
        wall_start = time.monotonic()

        # startup.mos reported the real Dymola version; publish it into the
        # run provenance as soon as a batch has come back (the persistent
        # lane does the equivalent via _probe_worker_version). Idempotent —
        # only the first batch that yields a version stamps it.
        stamped = [False]

        def _stamp_tool_version() -> None:
            if stamped[0]:
                return
            version = _read_startup_tool_version(self.config.work_dir)
            if version:
                stamped[0] = True
                setup.progress.update_metadata(tool_version=version)

        def _land(results: list[TestRunResult]) -> None:
            """Take delivery of one batch's results.

            Persisting each verdict here is what gives this lane the same
            abort-proof run record the default loop and the persistent
            dispatcher have: ``_prepare_run`` stamped every in-scope entry
            ``pending``, and only ``record_result`` clears it. Without this
            a completed ``run --batch`` left the whole scope ``pending`` and
            the manifest-wins protection never materialized for it.
            """
            all_results.extend(results)
            for r in results:
                manifest.record_result(r)
            _stamp_tool_version()

        if n_workers <= 1:
            # Sequential: run all batches in order on a single worker
            offset = 0
            for batch in batches:
                results = self._run_batch(
                    batch,
                    startup_path,
                    shutdown_path,
                    offset,
                    total,
                    worker_id=0,
                )
                _land(results)
                offset += len(batch)
        else:
            # Parallel: submit every batch to the pool; workers pull next
            # batch as they become free (natural load balancing + crash isolation).
            # worker_id attribution comes from the thread name so the dashboard can
            # group tests by actual worker slot rather than batch index.
            import re
            import threading
            from concurrent.futures import ThreadPoolExecutor, as_completed

            def _worker_slot() -> int:
                m = re.search(r"(\d+)$", threading.current_thread().name)
                return int(m.group(1)) if m else 0

            def _run(batch, offset):
                return self._run_batch(
                    batch,
                    startup_path,
                    shutdown_path,
                    offset,
                    total,
                    worker_id=_worker_slot(),
                )

            offset = 0
            futures = {}
            with ThreadPoolExecutor(
                max_workers=n_workers, thread_name_prefix="dym-worker"
            ) as pool:
                for batch in batches:
                    future = pool.submit(_run, batch, offset)
                    futures[future] = offset
                    offset += len(batch)

                for future in as_completed(futures):
                    _land(future.result())

        self._print_run_summary(all_results, total, time.monotonic() - wall_start)

        manifest.results = all_results
        setup.progress.finalize()
        return [manifest]

    def _run_batch(
        self,
        test_items: list[tuple[TestModel, str]],
        startup_path: Path,
        shutdown_path: Path,
        index_offset: int,
        total: int,
        worker_id: int | None = None,
    ) -> list[TestRunResult]:
        """Run a batch of tests in a single Dymola session."""
        work_dir = self.config.work_dir
        batch_id = index_offset  # Use offset as unique batch ID

        # Emit start events for all tests in this batch — we can't observe
        # individual test transitions within a batched Dymola session, but the
        # user at least knows which tests are currently in flight.
        if self.progress is not None:
            for _, test_key in test_items:
                self.progress.on_start(test_key, worker_id=worker_id)

        # Generate the batch script
        batch_script = _generate_batch_mos(
            test_items, startup_path, shutdown_path, work_dir, batch_id
        )

        # Calculate total timeout: per-test timeout * number of tests + startup overhead
        startup_overhead = 120  # seconds for library loading
        total_timeout = (
            sum(
                t.timeout if t.timeout is not None else self.config.timeout
                for t, _ in test_items
            )
            + startup_overhead
        )

        # Run the batch
        start_time = time.monotonic()

        # A run starts with a backend that was found (`require_found`).
        executable = self.executable_of(self.config)
        assert executable is not None
        try:
            cmd = [executable]
            if not self.dymola_config.show_ide:
                cmd.append("-nowindow")
            cmd.append(str(batch_script))

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(work_dir),
            )
            stdout, stderr = proc.communicate(timeout=total_timeout)
            batch_elapsed = time.monotonic() - start_time

        except subprocess.TimeoutExpired:
            batch_elapsed = time.monotonic() - start_time
            # review 2026-07-06 (finding 22): dymola.exe's dymosim child
            # inherits the pipes, so a bare kill()+communicate() blocks until
            # dymosim exits — the timeout path itself hung. Kill the whole
            # process tree first, then drain pipes with a bounded wait.
            kill_process_tree(proc.pid, name_hint="dymola")
            if proc.poll() is None:
                # The tree kill is a no-op when psutil is missing, the name
                # guard misses (a wrapper script's process name is not
                # "dymola") or the walk hits AccessDenied. We own this
                # Popen — kill it directly rather than fall into the
                # blocking communicate() finding 22 documents.
                try:
                    proc.kill()
                except (ProcessLookupError, OSError):
                    pass
            try:
                proc.communicate(timeout=10)
            except (subprocess.TimeoutExpired, OSError, ValueError):
                # Last resort: close pipes without blocking on stragglers.
                for stream in (proc.stdout, proc.stderr):
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError:
                            pass
            # review 2026-07-06 (finding 76): salvage tests that verifiably
            # completed on disk before the batch deadline (Dymola runs tests
            # sequentially — earlier tests in the batch usually finished);
            # only tests without a readable, complete result are timed out.
            results = []
            per_elapsed = batch_elapsed / len(test_items)
            for test, test_key in test_items:
                test_dir = work_dir / test_key
                completed, _why = _sim_completed_on_disk(
                    test_dir, float(test.stop_time)
                )
                if completed:
                    results.append(
                        TestRunResult(
                            model_id=test.model_id,
                            test_key=test_key,
                            success=True,
                            elapsed=per_elapsed,
                            statistics=parse_test_logs(test_dir),
                        )
                    )
                    if self.progress is not None:
                        self.progress.on_finish(
                            test_key,
                            success=True,
                            elapsed=per_elapsed,
                        )
                    continue
                results.append(
                    TestRunResult(
                        model_id=test.model_id,
                        test_key=test_key,
                        success=False,
                        elapsed=per_elapsed,
                        error_message=timeout_message(
                            f"Batch timed out after {total_timeout}s"
                        ),
                        timed_out=True,
                    )
                )
                if self.progress is not None:
                    self.progress.on_finish(
                        test_key,
                        success=False,
                        elapsed=per_elapsed,
                        detail="Batch timed out",
                        timed_out=True,
                    )
            return results

        except FileNotFoundError as missing:
            results = []
            for test, test_key in test_items:
                results.append(
                    TestRunResult(
                        model_id=test.model_id,
                        test_key=test_key,
                        success=False,
                        elapsed=0.0,
                        error_message=f"Dymola not found: {missing}",
                    )
                )
                if self.progress is not None:
                    self.progress.on_finish(
                        test_key,
                        success=False,
                        elapsed=0.0,
                        detail="Dymola not found",
                    )
            return results

        # Evaluate results per test: check if .mat file was produced
        results = []
        from ..base import _print_progress

        for i, (test, test_key) in enumerate(test_items):
            test_dir = work_dir / test_key
            mat_path = test_dir / "dsres.mat"
            short_name = test.model_id.rsplit(".", 1)[-1]
            label = f"{test_key} {short_name}"

            # Parse both dslog.txt (simulation runtime) and translation_log.txt (structural)
            statistics = parse_test_logs(test_dir)

            # Check for translation failure (defense in depth)
            translation_failed = False
            translation_log = test_dir / "translation_log.txt"
            if translation_log.exists():
                try:
                    tlog = translation_log.read_text(encoding="utf-8", errors="replace")
                    if "Translation aborted" in tlog or tlog.strip().endswith(
                        "= false"
                    ):
                        translation_failed = True
                except OSError:
                    pass

            # A simulation truly completed only if BOTH:
            #   1. dsfinal.txt exists (Dymola writes it at end of successful sim)
            #   2. mat file's last time reaches the requested stop_time
            # dsres.mat existence alone is insufficient — Dymola writes
            # incrementally, so a killed/aborted sim can leave a partial file.
            # Shared with the batch-timeout salvage path (finding 76).
            dsfinal_path = test_dir / "dsfinal.txt"
            sim_completed = False
            completion_msg: str | None = None
            if not translation_failed:
                sim_completed, completion_msg = _sim_completed_on_disk(
                    test_dir, float(test.stop_time)
                )

            per_elapsed = batch_elapsed / len(test_items)
            if sim_completed:
                _print_progress(
                    index_offset + i + 1,
                    total,
                    label,
                    "ok",
                    elapsed=per_elapsed,
                    quiet=self.quiet,
                )
                results.append(
                    TestRunResult(
                        model_id=test.model_id,
                        test_key=test_key,
                        success=True,
                        elapsed=per_elapsed,
                        statistics=statistics,
                    )
                )
                if self.progress is not None:
                    self.progress.on_finish(
                        test_key,
                        success=True,
                        elapsed=per_elapsed,
                    )
            else:
                if translation_failed:
                    msg = "Translation failed"
                elif not mat_path.exists():
                    msg = "No result file produced"
                elif not dsfinal_path.exists():
                    msg = "Simulation aborted (no dsfinal.txt)"
                else:
                    msg = completion_msg or "Simulation incomplete"
                # Try to get error from dslog
                dslog_path = test_dir / "dslog.txt"
                if dslog_path.exists():
                    try:
                        log_text = dslog_path.read_text(
                            encoding="utf-8", errors="replace"
                        )
                        if "ERROR" in log_text or "error" in log_text:
                            lines = log_text.strip().split("\n")
                            msg = msg + " | " + " | ".join(lines[-3:])
                    except OSError:
                        pass

                _print_progress(
                    index_offset + i + 1,
                    total,
                    label,
                    "FAIL",
                    elapsed=per_elapsed,
                    detail=msg[:80],
                    quiet=self.quiet,
                )
                results.append(
                    TestRunResult(
                        model_id=test.model_id,
                        test_key=test_key,
                        success=False,
                        elapsed=per_elapsed,
                        error_message=msg,
                        statistics=statistics,
                    )
                )
                if self.progress is not None:
                    self.progress.on_finish(
                        test_key,
                        success=False,
                        elapsed=per_elapsed,
                        detail=msg[:120],
                    )

        return results

    def read_result(
        self,
        test: TestModel,
        test_key: str,
        run_result: TestRunResult | None,
    ) -> TestResult:
        test_dir = self.config.work_dir / test_key
        # The run's own statistics (in process, or kept in run_stats.json)
        # win. Without them the logs Dymola left beside the result are read:
        # code that reads files tolerates a result written before
        # run_stats.json existed (D138), and such a result still has both.
        # They hold no phase walls, so nothing here invents a timing block.
        stats = (
            dict(run_result.statistics)
            if run_result and run_result.statistics
            else parse_test_logs(test_dir)
        )
        mat_path = test_dir / "dsres.mat"

        if not mat_path.exists():
            return TestResult(
                model_id=test.model_id,
                success=False,
                error_message=f"Result file not found: {mat_path}",
                statistics=stats,
            )

        # Phase 1: Get variable names (fast — reads only the name matrix)
        # Then resolve patterns to determine which variables we actually need.
        needed_vars = compute_needed_variables(
            mat_path,
            test,
            self.dymola_config.diagnostic_variables,
        )

        # Phase 2: Load only needed variables from the .mat file
        mat_data = read_result_mat(mat_path, variable_names=needed_vars)
        if mat_data is None:
            return TestResult(
                model_id=test.model_id,
                success=False,
                error_message=f"Failed to parse: {mat_path}",
                statistics=stats,
            )

        variables, diagnostics = extract_variables(
            mat_data,
            test,
            self.dymola_config.diagnostic_variables,
        )
        stats = fold_diagnostics_into_statistics(stats, diagnostics)

        unfinished = unfinished_result(test, mat_path, variables, diagnostics, stats)
        if unfinished is not None:
            return unfinished

        return TestResult(
            model_id=test.model_id,
            success=True,
            variables=variables,
            diagnostics=diagnostics,
            statistics=stats,
        )

    @classmethod
    def result_names(cls, test_dir: Path) -> list[str] | None:
        return result_files.mat_names(test_dir / "dsres.mat")

    @classmethod
    def result_series(cls, test_dir, names):
        return result_files.mat_series(test_dir / "dsres.mat", names)

    @classmethod
    def live_result_path(cls, test_dir: Path) -> Path:
        return test_dir / "dsres.mat"


# ---------------------------------------------------------------------------
# .mos script generation
# ---------------------------------------------------------------------------


#: startup.mos writes ``DymolaVersion()`` here. The batch lane has no live
#: session to ask for a version (that is what the persistent lane's
#: ``_probe_worker_version`` is for), and launching a Dymola purely to probe
#: would cost a licence seat and tens of seconds — so the script that is
#: already being run does the reporting, and the backend reads the file back
#: once a batch has returned. No file (MSL not loaded, Dymola never started)
#: leaves ``tool_version`` null, exactly as before.
TOOL_VERSION_FILENAME = "dymola_version.txt"


def _read_startup_tool_version(work_dir: Path) -> str | None:
    """First non-empty line of the version file startup.mos wrote, if any.

    Every worker's startup appends the same string, so the first line is the
    answer; a missing or unreadable file means "no honest version available".
    """
    try:
        text = (work_dir / TOOL_VERSION_FILENAME).read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return None
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return None


def _generate_startup_mos(config: Config, dymola_config: DymolaConfig) -> Path:
    """Generate startup.mos: load dependencies, main library, and setup commands."""
    work_dir = config.work_dir
    # Drop any version file a previous run in this work dir left behind — a
    # stale line would be read back as THIS run's provenance.
    (work_dir / TOOL_VERSION_FILENAME).unlink(missing_ok=True)
    lines = [
        "// Startup: load libraries and configure environment",
        f'cd("{work_dir.as_posix()}");',
    ]

    # Load dependencies first
    for dep_path in config.dependencies:
        dep_pkg = dependency_file(dep_path)
        lines.append(f'openModel("{dep_pkg.as_posix()}");')

    # Load main library
    lines.append(f'openModel("{(config.library_dir / "package.mo").as_posix()}");')

    # Dymola-specific framework settings (always enabled)
    lines.append("")
    lines.append("// Framework settings")
    lines.append("OutputCPUtime := true;")
    lines.append("Advanced.UI.TranslationInCommandLog := true;")

    # Run provenance: report the actual tool version to a file the backend
    # reads back (see TOOL_VERSION_FILENAME). Cheap — it rides along in a
    # script that has to run anyway.
    version_file = (work_dir / TOOL_VERSION_FILENAME).as_posix()
    lines.append("")
    lines.append("// Run provenance (read back by the backend)")
    lines.append(
        f'Modelica.Utilities.Streams.print(DymolaVersion(), "{version_file}");'
    )

    # Backend setup commands (e.g., OutputCPUtime = true)
    if dymola_config.backend_setup:
        lines.append("")
        lines.append("// Backend setup")
        for cmd in dymola_config.backend_setup:
            # Ensure command ends with semicolon
            cmd = cmd.strip()
            if not cmd.endswith(";"):
                cmd += ";"
            lines.append(cmd)

    script_path = work_dir / "startup.mos"
    script_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return script_path


def _generate_shutdown_mos(config: Config) -> Path:
    """Generate shutdown.mos: exit Dymola."""
    lines = [
        "// Shutdown",
        "Modelica.Utilities.System.exit();",
    ]
    script_path = config.work_dir / "shutdown.mos"
    script_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return script_path


def _generate_test_mos(
    test: TestModel,
    test_key: str,
    test_dir: Path,
) -> Path:
    """Generate a per-test .mos script: cd to test dir and simulate."""
    parts = [f'"{test.model_id}"']

    if test.stop_time != 1.0:
        if test.stop_time == int(test.stop_time):
            parts.append(f"stopTime={int(test.stop_time)}")
        else:
            parts.append(f"stopTime={test.stop_time}")

    # numberOfIntervals takes precedence over outputInterval if both are set
    if test.number_of_intervals is not None:
        parts.append(f"numberOfIntervals={test.number_of_intervals}")
    elif test.output_interval is not None:
        parts.append(f"outputInterval={test.output_interval}")

    parts.append(f'method="{test.method}"')
    parts.append(f"tolerance={test.tolerance}")
    parts.append('resultFile="dsres"')

    lines = [
        f"// {test.model_id}",
        f'cd("{test_dir.as_posix()}");',
        "clearlog();",
        f"simulateModel({','.join(parts)});",
        'savelog("translation_log.txt");',
    ]

    script_path = test_dir / "simulate.mos"
    script_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return script_path


def _generate_batch_mos(
    test_items: list[tuple[TestModel, str]],
    startup_path: Path,
    shutdown_path: Path,
    work_dir: Path,
    batch_id: int,
) -> Path:
    """Generate a batch .mos script that runs startup, all tests, then shutdown."""
    lines = [
        f"// Batch {batch_id}: {len(test_items)} tests",
        f'RunScript("{startup_path.as_posix()}");',
    ]

    for _test, test_key in test_items:
        test_mos = work_dir / test_key / "simulate.mos"
        lines.append(f'RunScript("{test_mos.as_posix()}");')

    lines.append(f'RunScript("{shutdown_path.as_posix()}");')

    script_path = work_dir / f"batch_{batch_id:04d}.mos"
    script_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return script_path


# ---------------------------------------------------------------------------
# Process / on-disk state helpers
# ---------------------------------------------------------------------------


def _sim_completed_on_disk(
    test_dir: Path,
    stop_time: float,
) -> tuple[bool, str | None]:
    """Check on-disk artifacts for a truly completed simulation.

    Completed iff dsfinal.txt exists AND dsres.mat's last time reaches
    stop_time (dsres.mat alone is insufficient — Dymola writes incrementally,
    so a killed sim leaves a partial file). Returns ``(completed, why_not)``;
    ``why_not`` is only set when artifacts exist but are short/unreadable.
    Used by both the normal batch evaluation and the batch-timeout salvage
    path (review 2026-07-06, finding 76).
    """
    from ..common.mat_reader import read_mat_time_extents

    mat_path = test_dir / "dsres.mat"
    dsfinal_path = test_dir / "dsfinal.txt"
    if not mat_path.exists() or not dsfinal_path.exists():
        return False, None
    extents = read_mat_time_extents(mat_path)
    if extents is None:
        return False, "dsres.mat unreadable"
    last_time = extents[1]
    # Allow a tiny tolerance — output_interval may not align perfectly
    tol = max(1e-6, abs(stop_time) * 1e-6)
    if last_time + tol >= stop_time:
        return True, None
    return False, f"Stopped early at T={last_time:.6g} of {stop_time:.6g}"
