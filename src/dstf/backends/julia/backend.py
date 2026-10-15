"""Julia / ModelingToolkit subprocess backend (D77).

Invokes a shipped driver script (``run_test.jl``) via ``julia --project``.
The driver loads the user's ``.jl`` file — which exports ``build_mtk_system()``
returning a ``NamedTuple{sys, u0, ps}`` — simulates via ``OrdinaryDiffEq``,
and writes a JSON result. The run/read machinery is the shared JSON-driver
skeleton (:class:`~..common.json_driver.JsonDriverBackend`); only the Julia
specifics live here.

This is the batch path — one Julia subprocess per test — reached with
``--batch``. The default is :class:`~.persistent_backend.PersistentJuliaBackend`
(D78), which keeps long-lived stdin-driven Julia workers alive so the
package-load and JIT cost is paid once per worker. On the batch path that
cost is paid per test: Julia's first-run JIT + precompile is several minutes
for MTK + OrdinaryDiffEq, seconds per test afterwards.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...config import Config
from .. import register
from ..base import Diagnosis
from ..common.json_driver import JsonDriverBackend
from ..locate import Located, find_executable
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
        # `Config` resolved the binary through `JuliaBackend.locate`; a path
        # that has since gone is as missing as one never found.
        found = JuliaBackend.executable_of(config)
        resolved = Path(found).expanduser() if found else None
        if resolved is None or not resolved.exists():
            raise RuntimeError(
                "Julia binary not found"
                + (
                    f" ({JuliaBackend.why_not_found(config)})"
                    if not found
                    else f": {found}"
                )
                + ". Install Julia (https://julialang.org/downloads/) and ensure "
                "'julia' is on PATH, or set \"executables\" in the Julia block of "
                'testing.json\'s "backends".'
            )
        project = config.source_path or Path.cwd()
        return cls(julia_binary=resolved, project_dir=project)


def _juliaup_folders() -> list[tuple[Path, str]]:
    """Where juliaup keeps what it installs, as ``find_executable`` takes
    them: its launcher, then each Julia version it downloaded. juliaup's
    installer offers to put the launcher on ``PATH`` and is often told
    no."""
    home = Path.home()
    name = "julia.exe" if sys.platform.startswith("win") else "julia"
    return [
        (home / ".juliaup" / "bin", name),
        (home / ".julia" / "juliaup", f"julia-*/bin/{name}"),
    ]


_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def _version(text: str) -> tuple[int, ...] | None:
    """The first ``major.minor[.patch]`` in ``text``, as numbers."""
    m = _VERSION_RE.search(text)
    return tuple(int(g) for g in m.groups() if g is not None) if m else None


def _newest_first(path: Path) -> tuple:
    """Sort key: juliaup's launcher (it follows the default channel), then
    its downloaded versions, the newest first."""
    version = _version(path.parent.parent.name) if "juliaup" in path.parts else None
    return (1, *(-n for n in version)) if version else (0,)


def manifest_julia_version(project_dir: Path) -> str | None:
    """The Julia version a project's ``Manifest.toml`` was resolved with
    (its ``julia_version`` line), or None: no manifest, or none it says."""
    try:
        text = (project_dir / "Manifest.toml").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r'^julia_version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else None


@register("Julia")
class JuliaBackend(JsonDriverBackend):
    """Subprocess-per-test Julia/MTK backend."""

    capabilities = frozenset(
        {
            Capability.BATCH_FALLBACK,  # subprocess-per-test (this class)
            Capability.PERSISTENT_WORKERS,  # via PersistentJuliaBackend (D78)
            # Deliberately absent:
            #   FMU_EXPORT — MTK.generate_fmu deferred
        }
    )
    produced_datasets = frozenset({DatasetType.TIME_SERIES})
    source_types = frozenset({"julia"})
    binaries = ("julia",)
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
    def persistent_backend_cls(cls):
        from .persistent_backend import PersistentJuliaBackend

        return PersistentJuliaBackend

    @classmethod
    def locate(cls, settings: Mapping[str, Any]) -> Located:
        """``julia``: the block's ``executables``, then ``PATH``, then
        juliaup's own folders under the home directory."""
        return Located(
            (
                find_executable(
                    settings,
                    cls.binaries,
                    folders=_juliaup_folders(),
                    best_first=_newest_first,
                ),
            )
        )

    @classmethod
    def diagnose(
        cls, located: Located, *, entry: str = "", config: Config | None = None
    ) -> Diagnosis:
        """The located ``julia``, the version it reports, and — for a Julia
        project with a ``Manifest.toml`` — whether that version is at least
        the one the manifest was resolved with: an older Julia cannot be
        relied on to load a newer one's manifest."""
        lines = located.lines(entry)
        if located.executable is None:
            return Diagnosis(
                tuple(lines),
                False,
                (
                    "Install Julia from https://julialang.org/downloads/ (or with "
                    '`juliaup`), or set "executables" in the Julia block of '
                    'testing.json\'s "backends"',
                ),
            )
        try:
            out = subprocess.run(
                [located.executable, "--version"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            banner = (out.stdout or out.stderr or "").strip()
        except (OSError, subprocess.SubprocessError) as exc:
            lines.append(f"  version: could not be read ({exc})")
            return Diagnosis(tuple(lines), False, ("Check that this julia starts",))
        lines.append(f"  version: {banner or '(no output)'}")
        usable, fixes = True, ()
        project = config.source_path if config is not None else None
        needed = (
            manifest_julia_version(project)
            if project is not None
            and config is not None
            and config.source_type == "julia"
            else None
        )
        if needed is not None:
            have, need = _version(banner), _version(needed)
            met = have is not None and need is not None and have[:2] >= need[:2]
            minimum = ".".join(str(n) for n in need[:2]) if need else needed
            lines.append(
                f"  needed: Julia {minimum} or newer — {project / 'Manifest.toml'} "
                f"was resolved with {needed}: {'met' if met else 'NOT met'}"
            )
            if not met:
                usable = False
                fixes = (
                    f"Install Julia {minimum} or newer, or resolve the project "
                    "again with this one (Pkg.resolve)",
                )
        lines.append(
            "  note: a project's first run pays a multi-minute ModelingToolkit / "
            "OrdinaryDiffEq compile; later runs use the cache"
        )
        return Diagnosis(tuple(lines), usable, fixes)

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
