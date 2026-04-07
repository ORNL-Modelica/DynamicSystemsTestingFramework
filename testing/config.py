"""Configuration constants and path resolution for the Modelica testing system."""

import json
import platform
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# Default simulation parameters (Dymola defaults)
DEFAULT_STOP_TIME = 1.0
DEFAULT_TOLERANCE = 1e-4
DEFAULT_METHOD = "Dassl"
DEFAULT_NUMBER_OF_INTERVALS = 500

# Comparison defaults
DEFAULT_COMPARISON_TOLERANCE = 1e-4

# Config filename looked for in the library root or working directory
CONFIG_FILENAME = "testing.json"

# Supported simulators
SIMULATORS = ("Dymola", "OpenModelica")


def detect_os() -> str:
    """Detect the current OS for reference result partitioning."""
    system = platform.system().lower()
    if system == "linux":
        return "linux"
    elif system == "darwin":
        return "macos"
    elif system == "windows":
        return "windows"
    return system


def find_library_root(start: Optional[Path] = None) -> Path:
    """Find a Modelica library root by looking for a top-level package.mo.

    Walks up from `start` looking for a directory that contains a subdirectory
    with a package.mo file (the standard Modelica library layout).
    """
    if start is None:
        start = Path.cwd()
    path = start.resolve()
    for _ in range(10):
        for child in path.iterdir():
            if child.is_dir() and (child / "package.mo").exists():
                return path
        path = path.parent
    raise FileNotFoundError(
        f"Could not find a Modelica library root from {start}. "
        "Expected a directory containing <LibraryName>/package.mo."
    )


def detect_library_name(library_root: Path) -> str:
    """Detect the Modelica library name from the top-level package.mo."""
    for child in sorted(library_root.iterdir()):
        if not child.is_dir():
            continue
        pkg_file = child / "package.mo"
        if pkg_file.exists():
            try:
                text = pkg_file.read_text(encoding="utf-8", errors="replace")
                m = re.search(r'package\s+(\w+)', text)
                if m:
                    return m.group(1)
            except OSError:
                continue
    raise FileNotFoundError(
        f"No Modelica package found in {library_root}. "
        "Expected <LibraryName>/package.mo."
    )


def load_config_file(path: Path) -> dict:
    """Load a testing.json config file."""
    if path.is_dir():
        path = path / CONFIG_FILENAME
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


@dataclass
class Config:
    """Runtime configuration for the testing system.

    All paths are explicit — no assumptions about where the testing tool
    lives relative to the library or references.
    """
    # Required: path to the Modelica library to test
    library_root: Optional[Path] = None
    library_name: Optional[str] = None

    # Reference results location (can be a separate repo/directory)
    reference_root: Optional[Path] = None

    # Simulator
    simulator: str = "Dymola"
    dymola_path: str = "dymola"

    # OS override (auto-detected if not set)
    os_name: Optional[str] = None

    # Paths to dependency library roots (directories containing package.mo)
    dependencies: list[str] = field(default_factory=list)

    # Simulation / comparison
    parallel: int = 1
    tolerance: float = DEFAULT_COMPARISON_TOLERANCE
    final_only: bool = False
    timeout: int = 600

    # Output
    work_dir: Optional[Path] = None

    # Filename shortening for references
    path_abbreviations: dict[str, str] = field(default_factory=dict)

    # .mos file name
    mos_filename: Optional[str] = None

    # Config file path (optional — loaded explicitly or auto-discovered)
    config_file: Optional[Path] = None

    def __post_init__(self):
        # Load config file if specified, or look in library root / cwd
        file_config = {}
        if self.config_file:
            file_config = load_config_file(Path(self.config_file))
        elif self.library_root:
            file_config = load_config_file(Path(self.library_root))
        else:
            file_config = load_config_file(Path.cwd())

        # Resolve library root
        if self.library_root is None:
            lib_path = file_config.get("library_path")
            if lib_path:
                self.library_root = Path(lib_path).resolve()
            else:
                self.library_root = find_library_root()
        else:
            self.library_root = Path(self.library_root).resolve()

        # Auto-detect library name
        if self.library_name is None:
            self.library_name = file_config.get(
                "library_name", detect_library_name(self.library_root)
            )

        # OS detection
        if self.os_name is None:
            self.os_name = file_config.get("os", detect_os())

        # Simulator
        if "simulator" in file_config:
            self.simulator = file_config["simulator"]

        # Reference root: CLI arg > config file > error
        if self.reference_root is None:
            ref_path = file_config.get("reference_root")
            if ref_path:
                self.reference_root = Path(ref_path).resolve()
            # else: left as None — commands that need it will raise an error
        else:
            self.reference_root = Path(self.reference_root).resolve()

        # Dependencies
        if not self.dependencies and "dependencies" in file_config:
            self.dependencies = file_config["dependencies"]

        # Path abbreviations
        if not self.path_abbreviations and "path_abbreviations" in file_config:
            self.path_abbreviations = file_config["path_abbreviations"]

        # MOS filename
        if self.mos_filename is None:
            self.mos_filename = file_config.get("mos_file", "runAll_Dymola.mos")

        # Work directory
        if self.work_dir is None:
            work = file_config.get("work_dir")
            if work:
                self.work_dir = Path(work).resolve()
            else:
                self.work_dir = Path.cwd() / "testing_output"
        else:
            self.work_dir = Path(self.work_dir).resolve()

    @property
    def library_dir(self) -> Path:
        """Path to the library's top-level package directory."""
        return self.library_root / self.library_name

    @property
    def mos_file(self) -> Path:
        return self.library_root / self.mos_filename

    def _require_reference_root(self) -> Path:
        if self.reference_root is None:
            raise ValueError(
                "No reference_root configured. Set it via:\n"
                "  --reference-root /path/to/references\n"
                "  or in testing.json: {\"reference_root\": \"/path/to/references\"}"
            )
        return self.reference_root

    @property
    def reference_dir(self) -> Path:
        """Reference results directory, partitioned by simulator and OS.

        Structure: <reference_root>/<Simulator>/<os>/
        e.g.:      references/Dymola/linux/
        """
        return self._require_reference_root() / self.simulator / self.os_name

    @property
    def index_file(self) -> Path:
        return self.reference_dir / "index.json"
