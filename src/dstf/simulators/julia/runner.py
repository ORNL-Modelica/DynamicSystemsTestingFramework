"""Julia / ModelingToolkit subprocess runner (D77).

Invokes a shipped driver script (``run_test.jl``) via ``julia --project``.
The driver loads the user's ``.jl`` file — which exports ``build_mtk_system()``
returning a ``NamedTuple{sys, u0, ps}`` — simulates via ``OrdinaryDiffEq``,
and writes a JSON result. The run/read machinery is the shared JSON-driver
skeleton (:class:`~..common.json_driver.JsonDriverRunner`); only the Julia
specifics live here.

This is the batch path — one Julia subprocess per test — reached with
``--batch``. The default is :class:`~.persistent_runner.PersistentJuliaRunner`
(D78), which keeps long-lived stdin-driven Julia workers alive so the
package-load and JIT cost is paid once per worker. On the batch path that
cost is paid per test: Julia's first-run JIT + precompile is several minutes
for MTK + OrdinaryDiffEq, seconds per test afterwards.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ...config import Config
from .. import register
from ..common.json_driver import JsonDriverRunner
from ..types import Capability, DatasetType

logger = logging.getLogger(__name__)


# Shipped driver script; copied/resolved at runtime.
_DRIVER_PATH = Path(__file__).resolve().parent / "run_test.jl"


@dataclass(frozen=True)
class JuliaConfig:
    """Julia-specific settings extracted from the unified :class:`Config`.

    ``julia_binary`` is the absolute path to the ``julia`` executable.
    ``project_dir`` is the Julia project directory (contains ``Project.toml``
    and ``Manifest.toml``); defaults to the same directory the test sources
    live in (``config.source_path``). That way a user ships a self-contained
    Julia project with MTK pinned.
    """

    julia_binary: Path
    project_dir: Path

    @classmethod
    def from_config(cls, config: Config) -> JuliaConfig:
        # Config.simulator_path is already resolved in __post_init__ via the
        # simulators map + PATH fallback. If that lookup didn't produce
        # anything (e.g., user omitted the path and PATH doesn't include
        # julia), try shutil.which() once more before giving up.
        resolved: Path | None = None
        if config.simulator_path:
            p = Path(config.simulator_path).expanduser()
            if p.exists():
                resolved = p
        if resolved is None:
            on_path = shutil.which("julia")
            if on_path:
                resolved = Path(on_path)
        if resolved is None:
            raise RuntimeError(
                "Julia binary not found. Install Julia (https://julialang.org/"
                "downloads/) and ensure 'julia' is on PATH, or set an explicit "
                "path under testing.json's 'simulators' map."
            )
        project = config.source_path or Path.cwd()
        return cls(julia_binary=resolved, project_dir=project)


@register("Julia")
class JuliaRunner(JsonDriverRunner):
    """Subprocess-per-test Julia/MTK runner."""

    capabilities = frozenset(
        {
            Capability.BATCH_FALLBACK,  # subprocess-per-test (this class)
            Capability.PERSISTENT_WORKERS,  # via PersistentJuliaRunner (D78)
            # Deliberately absent:
            #   FMU_EXPORT — MTK.generate_fmu deferred
        }
    )
    produced_datasets = frozenset({DatasetType.TIME_SERIES})
    artifact_files = (
        ("result.json", "Simulation result (time + variables)"),
        ("julia_stdout.txt", "Julia stdout"),
        ("julia_stderr.txt", "Julia stderr"),
    )

    driver_path = _DRIVER_PATH
    tool_label = "Julia"
    action_label = "simulation"
    stdio_prefix = "julia"
    source_suffix = ".jl"

    def __init__(self, config: Config):
        super().__init__(config)
        # Resolve once so run_single_test errors are loud + early when
        # the binary is missing; lets --filter walk the test set quickly.
        self.julia_config = JuliaConfig.from_config(config)

    @classmethod
    def persistent_runner_cls(cls):
        from .persistent_runner import PersistentJuliaRunner

        return PersistentJuliaRunner

    @property
    def tool_binary(self) -> Path:
        return self.julia_config.julia_binary

    def build_command(
        self,
        binary: Path,
        driver: Path,
        user_file: Path,
        stop_time: float,
        tolerance: float,
        result_path: Path,
    ) -> list[str]:
        return [
            str(binary),
            f"--project={self.julia_config.project_dir}",
            "--startup-file=no",
            "--color=no",
            str(driver),
            str(user_file),
            str(stop_time),
            str(tolerance),
            str(result_path),
        ]

    def describe_tool_version(self) -> str | None:
        """Version of the configured ``julia`` binary. It is a property of the
        binary, not of a running session, so both the batch and the persistent
        lane get it from here — the persistent template stamps provenance
        through ``describe_tool_version`` before its workers are up, so no
        ``_probe_worker_version`` override is needed. Best-effort: ``None`` on
        any failure."""
        try:
            out = subprocess.run(
                [str(self.julia_config.julia_binary), "--version"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            banner = (out.stdout or out.stderr or "").strip()
            return banner or None
        except Exception:
            return None
