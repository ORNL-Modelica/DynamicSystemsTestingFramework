"""Python / subprocess backend (D80).

Invokes a shipped driver script (``run_test.py``) via the configured
Python interpreter. The driver loads the user's ``.py`` file — which
must define ``simulate(stop_time, tolerance) -> dict`` — executes it,
and writes a JSON result. The run/read machinery is the shared JSON-driver
skeleton (:class:`~..common.json_driver.JsonDriverBackend`); only the Python
specifics live here.

Batch-only for the MVP; each test spawns one Python subprocess. Startup
cost is roughly 30-100 ms per test (importlib + any user-file imports);
for trivial tests this dominates over the actual ``simulate()`` call.
A persistent-worker path (long-lived Python subprocess with stdin-JSON
dispatch) is a future enhancement if per-test overhead becomes an issue
— mirrors the D77 → D78 Julia progression.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ...config import Config
from .. import register
from ..common.json_driver import JsonDriverBackend
from ..types import Capability, DatasetType

logger = logging.getLogger(__name__)


# Shipped driver script.
_DRIVER_PATH = Path(__file__).resolve().parent / "run_test.py"


@dataclass(frozen=True)
class PythonConfig:
    """Python-specific settings extracted from the unified :class:`Config`.

    ``python_binary`` is the absolute path to the Python interpreter that
    will run user test scripts. It must have whatever packages the user's
    scripts import (scipy, pandas, ...). The framework does not manage
    the user's environment — pick a ``python`` that has their deps.
    """

    python_binary: Path

    @classmethod
    def from_config(cls, config: Config) -> PythonConfig:
        resolved: Path | None = None
        if config.backend_path:
            p = Path(config.backend_path).expanduser()
            if p.exists():
                resolved = p
        if resolved is None:
            for name in ("python", "python3"):
                on_path = shutil.which(name)
                if on_path:
                    resolved = Path(on_path)
                    break
        if resolved is None:
            raise RuntimeError(
                "Python binary not found. Ensure 'python' or 'python3' is on "
                "PATH, or set an explicit path under testing.json's "
                '\'backends\' map, e.g. {"Python": ["/path/to/venv/bin/python"]}.'
            )
        return cls(python_binary=resolved)


@register("Python")
class PythonBackend(JsonDriverBackend):
    """Subprocess-per-test Python backend."""

    capabilities = frozenset(
        {
            Capability.BATCH_FALLBACK,
            # Each test is its own subprocess: killing it stops the test.
            Capability.CANCEL,
            # Deliberately absent:
            #   PERSISTENT_WORKERS — stdin-driven long-lived Python process deferred.
            #   FMU_EXPORT — not meaningful for arbitrary Python scripts.
        }
    )
    produced_datasets = frozenset({DatasetType.TIME_SERIES})
    artifact_files = (
        ("result.json", "Simulation result (time + variables)"),
        ("python_stdout.txt", "Python stdout"),
        ("python_stderr.txt", "Python stderr"),
    )

    driver_path = _DRIVER_PATH
    tool_label = "Python"
    action_label = "execution"
    stdio_prefix = "python"
    source_suffix = ".py"

    def __init__(self, config: Config):
        super().__init__(config)
        self.python_config = PythonConfig.from_config(config)

    @property
    def tool_binary(self) -> Path:
        return self.python_config.python_binary

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
            str(driver),
            str(user_file),
            str(stop_time),
            str(tolerance),
            str(result_path),
        ]

    def describe_tool_version(self) -> str | None:
        """Version of the *configured* interpreter (``python_config.
        python_binary``) that actually runs each test's subprocess — not this
        host's Python, which may differ. Probed via ``--version`` with a short
        timeout; ``None`` on any failure."""
        try:
            out = subprocess.run(
                [str(self.python_config.python_binary), "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            # Older CPython prints the banner to stderr, newer to stdout.
            banner = (out.stdout or out.stderr or "").strip()
            return banner or None
        except Exception:
            return None
