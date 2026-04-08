"""Configuration constants and path resolution for the Modelica testing system."""

import json
import logging
import platform
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default simulation parameters (Dymola defaults)
DEFAULT_STOP_TIME = 1.0
DEFAULT_TOLERANCE = 1e-4
DEFAULT_METHOD = "Dassl"
DEFAULT_NUMBER_OF_INTERVALS = 500

# Comparison defaults
DEFAULT_COMPARISON_TOLERANCE = 1e-4

# Config filename looked for near the package or in working directory
CONFIG_FILENAME = "testing.json"

# Maps simulator entry names to their backend type
SIMULATOR_BACKENDS = {
    "Dymola": "Dymola",
    "OpenModelica": "OpenModelica",
}


def _detect_backend(simulator_name: str) -> str:
    """Determine the simulator backend type from a named entry.

    E.g., "Dymola 2025" -> "Dymola", "OpenModelica 1.23" -> "OpenModelica"
    """
    for prefix, backend in SIMULATOR_BACKENDS.items():
        if simulator_name.startswith(prefix):
            return backend
    return simulator_name


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


def find_package_dir(start: Optional[Path] = None) -> Path:
    """Find a Modelica package directory containing package.mo.

    If start is a directory containing package.mo, returns it directly.
    Otherwise walks up looking for a directory that contains a subdirectory
    with package.mo.
    """
    if start is None:
        start = Path.cwd()
    path = start.resolve()

    # Check if start itself is a package dir
    if (path / "package.mo").exists():
        return path

    # Walk up looking for a child dir with package.mo
    for _ in range(10):
        for child in path.iterdir():
            if child.is_dir() and (child / "package.mo").exists():
                return child
        path = path.parent

    raise FileNotFoundError(
        f"Could not find a Modelica package from {start}. "
        "Expected a directory containing package.mo."
    )


def read_package_name(package_dir: Path) -> str:
    """Read the Modelica package name from package.mo."""
    pkg_file = package_dir / "package.mo"
    if not pkg_file.exists():
        raise FileNotFoundError(f"No package.mo in {package_dir}")
    text = pkg_file.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'package\s+(\w+)', text)
    if m:
        return m.group(1)
    raise ValueError(f"Could not parse package name from {pkg_file}")


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


def _create_default_config(config_dir: Path, library_name: str) -> dict:
    """Create a default testing.json and return its contents."""
    config = {
        "simulator": "Dymola",
        "simulators": {},
        "dependencies": [],
    }
    config_path = config_dir / CONFIG_FILENAME
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    logger.info("Created default %s at %s", CONFIG_FILENAME, config_path)
    print(f"Created default {CONFIG_FILENAME} at {config_path}")
    return config


def _resolve_simulator_path(
    simulators_config: dict[str, list[str]],
    simulator_name: str,
) -> Optional[str]:
    """Resolve a simulator executable path from the simulators config.

    Looks up the named entry and returns the first path that exists on disk.
    Falls back to checking if the name is on PATH.
    """
    paths = simulators_config.get(simulator_name, [])
    for p in paths:
        path = Path(p)
        if path.is_absolute() and path.exists():
            return str(path)
        found = shutil.which(p)
        if found:
            return found
    return None


@dataclass
class Config:
    """Runtime configuration for the testing system.

    The primary input is package_path — the directory containing package.mo
    for the Modelica library being tested.
    """

    # Path to the Modelica package directory (contains package.mo)
    package_path: Optional[Path] = None

    # Reference results location (can be a separate repo/directory)
    reference_root: Optional[Path] = None

    # Simulator selection
    simulator: str = "Dymola"
    simulator_path: Optional[str] = None
    show_ide: bool = False
    simulator_setup: list[str] = field(default_factory=list)  # Commands run after loading libraries

    # OS override (auto-detected if not set)
    os_name: Optional[str] = None

    # Paths to dependency library roots
    dependencies: list[str] = field(default_factory=list)

    # Simulation / comparison
    parallel: int = 1
    tolerance: float = DEFAULT_COMPARISON_TOLERANCE
    final_only: bool = False
    timeout: int = 600

    # Output
    work_dir: Optional[Path] = None

    # Test spec file (external test definitions)
    test_spec_file: Optional[Path] = None

    # Diagnostic variables: auto-captured from simulation, shown in reports but not compared
    diagnostic_variables: list[str] = field(default_factory=lambda: ["CPUtime", "EventCounter"])

    # Config file path
    config_file: Optional[Path] = None

    # Resolved (set during __post_init__)
    library_name: Optional[str] = None

    def __post_init__(self):
        # Resolve reference root early — needed for config file search
        if self.reference_root is not None:
            self.reference_root = Path(self.reference_root).resolve()

        # Load config file first — it may provide package_path
        file_config = {}
        config_found_dir = None
        if self.config_file:
            config_found_dir = Path(self.config_file).resolve().parent
            file_config = load_config_file(Path(self.config_file))
        else:
            # Build search dirs from what we know so far
            search_dirs = [Path.cwd()]
            if self.reference_root is not None:
                search_dirs.insert(0, self.reference_root)
            if self.package_path is not None:
                pkg = Path(self.package_path).resolve()
                search_dirs.insert(0, pkg)        # package dir
                search_dirs.insert(0, pkg.parent)  # repo root
            for search_dir in search_dirs:
                file_config = load_config_file(search_dir)
                if file_config:
                    config_found_dir = search_dir.resolve()
                    break

        # Resolve package path — CLI arg > config file > auto-detect
        if self.package_path is not None:
            self.package_path = Path(self.package_path).resolve()
            if not (self.package_path / "package.mo").exists():
                self.package_path = find_package_dir(self.package_path)
        elif "package_path" in file_config:
            base_dir = config_found_dir or Path.cwd()
            self.package_path = (base_dir / file_config["package_path"]).resolve()
            if not (self.package_path / "package.mo").exists():
                self.package_path = find_package_dir(self.package_path)
        else:
            self.package_path = find_package_dir()

        # Read library name from package.mo
        if self.library_name is None:
            self.library_name = read_package_name(self.package_path)

        # The parent of the package dir is where testing.json typically lives
        repo_root = self.package_path.parent

        # If no config was found yet (package_path wasn't available for search),
        # try repo_root now
        if not file_config and config_found_dir is None:
            file_config = load_config_file(repo_root)
            if file_config:
                config_found_dir = repo_root.resolve()

        # Auto-create testing.json if none was found
        if not file_config:
            config_dir = self.reference_root if self.reference_root else repo_root
            file_config = _create_default_config(config_dir, self.library_name)

        # OS detection
        if self.os_name is None:
            self.os_name = file_config.get("os", detect_os())

        # Simulator selection
        if "simulator" in file_config and self.simulator == "Dymola":
            self.simulator = file_config["simulator"]

        # Show IDE
        if not self.show_ide and "show_ide" in file_config:
            self.show_ide = file_config["show_ide"]

        # Simulator setup commands
        if not self.simulator_setup and "simulator_setup" in file_config:
            self.simulator_setup = file_config["simulator_setup"]

        # Diagnostic variables
        if "diagnostic_variables" in file_config:
            self.diagnostic_variables = file_config["diagnostic_variables"]

        # Resolve simulator executable path
        if self.simulator_path is None:
            simulators_config = file_config.get("simulators", {})
            if simulators_config:
                resolved = _resolve_simulator_path(simulators_config, self.simulator)
                if resolved:
                    self.simulator_path = resolved
                else:
                    logger.warning(
                        "No working path found for simulator '%s' in config. "
                        "Falling back to PATH lookup.",
                        self.simulator,
                    )
            if self.simulator_path is None:
                backend = self.simulator_backend.lower()
                self.simulator_path = shutil.which(backend) or backend

        # Reference root (may already be set from CLI arg)
        if self.reference_root is None:
            ref_path = file_config.get("reference_root")
            if ref_path:
                base_dir = config_found_dir or repo_root
                self.reference_root = (base_dir / ref_path).resolve()
            else:
                # Default: if testing.json lives in a ReferenceResults dir, use that;
                # otherwise fall back to <repo>/Resources/ReferenceResults
                if config_found_dir and config_found_dir.name == "ReferenceResults":
                    self.reference_root = config_found_dir
                else:
                    self.reference_root = repo_root / "Resources" / "ReferenceResults"

        # Dependencies — resolve relative paths from config file location
        if not self.dependencies and "dependencies" in file_config:
            base_dir = config_found_dir or repo_root
            resolved_deps = []
            for dep in file_config["dependencies"]:
                dep_path = Path(dep)
                if not dep_path.is_absolute():
                    dep_path = (base_dir / dep_path).resolve()
                resolved_deps.append(str(dep_path))
            self.dependencies = resolved_deps

        # Test spec file — resolve relative to where testing.json was found
        if self.test_spec_file is None:
            spec_path = file_config.get("test_spec")
            if spec_path:
                base_dir = config_found_dir or repo_root
                self.test_spec_file = (base_dir / spec_path).resolve()
        else:
            self.test_spec_file = Path(self.test_spec_file).resolve()

        # Work directory
        if self.work_dir is None:
            work = file_config.get("work_dir")
            if work:
                self.work_dir = Path(work).resolve()
            else:
                sim_dir = self.simulator.replace(" ", "_")
                self.work_dir = (
                    Path.cwd() / "testing_output"
                    / self.library_name / sim_dir / self.os_name
                )
        else:
            self.work_dir = Path(self.work_dir).resolve()

    @property
    def simulator_backend(self) -> str:
        """The simulator backend type (e.g., 'Dymola' from 'Dymola 2025')."""
        return _detect_backend(self.simulator)

    @property
    def library_dir(self) -> Path:
        """Path to the library's top-level package directory (contains package.mo)."""
        return self.package_path

    @property
    def reference_dir(self) -> Path:
        """Reference results directory, partitioned by simulator backend and OS."""
        return self.reference_root / self.simulator_backend / self.os_name

