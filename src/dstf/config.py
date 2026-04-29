"""Configuration constants and path resolution for the testing system."""

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
    "FMPy": "FMPy",
    "Julia": "Julia",
}

# Binary names by backend, used for PATH-lookup fallback when testing.json
# doesn't list an explicit path. Backend-name lowercase happens to match for
# Dymola; OpenModelica's binary is ``omc`` (not ``openmodelica``). An empty
# string marks backends that don't ship a standalone binary (FMPy is a
# Python library — simulator_path is unused for it).
BACKEND_BINARY_NAMES = {
    "Dymola": "dymola",
    "OpenModelica": "omc",
    "FMPy": "",
    "Julia": "julia",
    "Python": "python",
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


def _auto_detect_simulator(
    simulators_config: dict[str, list[str]],
) -> Optional[tuple[str, str]]:
    """Pick the first simulator in ``simulators_config`` whose binary resolves.

    Resolution order per candidate entry:
      1. Any path in its ``simulators`` list that exists on disk or resolves
         on ``PATH`` (via :func:`_resolve_simulator_path`).
      2. If the list contains no absolute paths (empty or bare-binary names
         only), fall back to the backend's canonical binary
         (``BACKEND_BINARY_NAMES``) on ``PATH``.

    The fallback is gated on "no absolute paths given" because a list of
    OS-specific absolute paths (e.g. ``C:\\Program Files\\...Dymola.exe``)
    is a strong signal that the user wants *that* install — if none of the
    absolute paths resolve, treating some same-named binary on PATH as
    equivalent would silently run the wrong simulator on a mixed-OS setup
    (Linux WSL often has a ``dymola`` symlink that points at a binary that
    isn't usable the same way).

    Iteration follows dict insertion order so users can express preference
    (list Dymola first on Windows; list OpenModelica first on Linux) —
    "first available wins" across both Windows and Linux machines with the
    same ``testing.json``. Returns ``(simulator_name, resolved_path)`` or
    ``None`` if no entry resolves.
    """
    for name, paths in simulators_config.items():
        path = _resolve_simulator_path(simulators_config, name)
        if path:
            return name, path
        # No explicit entry resolved. Only fall back to a generic PATH
        # lookup when the user hasn't pinned this backend to platform-
        # specific paths. An entirely-pinned list is a no-fallback
        # instruction. We detect "looks like a path" via a Windows-drive
        # regex OR POSIX-absolute — pathlib's ``is_absolute`` alone returns
        # False for ``C:\...`` on Linux (PosixPath doesn't recognize
        # Windows drive letters).
        has_pinned_path = any(_looks_like_path(p) for p in paths)
        if has_pinned_path:
            continue
        backend = _detect_backend(name)
        binary = BACKEND_BINARY_NAMES.get(backend, backend.lower())
        if binary:
            found = shutil.which(binary)
            if found:
                return name, found
    return None


_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _looks_like_path(entry: str) -> bool:
    """True if ``entry`` is an OS-specific path (not just a bare binary name).

    POSIX-absolute (``/usr/bin/omc``) or Windows-drive-rooted
    (``C:\\Program Files\\...``) count as pinned paths; bare names like
    ``"omc"`` or ``"dymola.exe"`` do not.
    """
    if entry.startswith("/"):
        return True
    if _WINDOWS_DRIVE_RE.match(entry):
        return True
    return False


@dataclass
class Config:
    """Runtime configuration for the testing system.

    The primary input is source_path — for source_type == "modelica" this is
    the directory containing package.mo for the library being tested; for FMU
    / Julia / data-file sources it generalizes to that source location.
    """

    # Source type: "modelica" (default, `source_path` points at a package.mo dir),
    # "fmu" (Phase 2), "julia" / "simulink" / "data-file" (future). See docs/vision.md.
    # Discovery + backend selection gate on this.
    source_type: str = "modelica"

    # Path to the source location for the library being tested.
    # For source_type == "modelica": the directory containing package.mo.
    # For other source types: the FMU directory / Julia script / CSV file / etc.
    source_path: Optional[Path] = None

    # Reference results location (can be a separate repo/directory)
    reference_root: Optional[Path] = None

    # Simulator selection. ``None`` means "not explicitly chosen" — the
    # post-init resolution then consults testing.json's ``simulator`` key,
    # then auto-detect from the ``simulators`` map, then falls back to
    # ``"Dymola"`` as the historical default. A non-None value (typically
    # from a CLI ``--simulator`` flag) is treated as authoritative and is
    # NOT overridden by anything from testing.json.
    simulator: Optional[str] = None
    simulator_path: Optional[str] = None
    show_ide: bool = False
    simulator_setup: list[str] = field(default_factory=list)  # Commands run after loading libraries

    # OS override (auto-detected if not set)
    os_name: Optional[str] = None

    # Paths to dependency library roots
    dependencies: list[str] = field(default_factory=list)

    # Simulation / comparison
    parallel: int = 1
    batch_size: Optional[int] = None  # tests per Dymola session; None = ceil(total/parallel) (one big batch per worker)
    tolerance: float = DEFAULT_COMPARISON_TOLERANCE
    default_points: bool = False
    timeout: int = 60

    # Output
    work_dir: Optional[Path] = None

    # Optional override for Dymola's Python interface archive (dymola.egg or dymola-*.whl).
    # If unset, auto-discovers under platform install roots.
    dymola_interface_path: Optional[Path] = None

    # Test spec file (external test definitions)
    test_spec_file: Optional[Path] = None

    # Diagnostic variables: auto-captured from simulation, shown in reports but not compared
    diagnostic_variables: list[str] = field(default_factory=lambda: ["CPUtime", "EventCounter"])

    # Phase 6.0 — interactive.html payload budget. LTTB-decimates trajectories
    # embedded for Plotly rendering; full-resolution arrays remain on disk in
    # comparison_data.json. Only affects the HTML visual; pass/fail scoring,
    # stored baselines, and the data sidecar are unaffected. Default 1000 keeps
    # a 50-var × 5000-sample test under the ~5 MB budget given today's
    # per-variable time-array duplication; the follow-up dedup (idea #47)
    # will let this rise to 2000 at the same budget.
    max_embedded_samples: int = 1000

    # User-provided recognizers (PTA.3) — parsed from testing.json's
    # "recognizers" list via discovery.json_recognizer.parse_recognizer_spec.
    # Stored on Config so they're scoped to this run (no module-registry leak
    # across multiple Config instances or between tests).
    recognizers: list = field(default_factory=list)

    # Names of bundled recognizers to disable (PTA.3). E.g.,
    # ["modelica:bundled-unit-tests"] when the user has only custom-convention
    # tests and wants the bundled UnitTests recognizer off.
    disabled_bundled: list[str] = field(default_factory=list)

    # Config file path
    config_file: Optional[Path] = None

    # Resolved (set during __post_init__)
    library_name: Optional[str] = None

    def __post_init__(self):
        # Resolve reference root early — needed for config file search
        if self.reference_root is not None:
            self.reference_root = Path(self.reference_root).resolve()

        # Load config file first — it may provide source_path
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
            if self.source_path is not None:
                pkg = Path(self.source_path).resolve()
                search_dirs.insert(0, pkg)        # package dir
                search_dirs.insert(0, pkg.parent)  # repo root
            for search_dir in search_dirs:
                file_config = load_config_file(search_dir)
                if file_config:
                    config_found_dir = search_dir.resolve()
                    break

        # Source type peek — controls whether we look for a Modelica package.
        # Done before source_path resolution so non-modelica backends (FMU,
        # data-file, ...) can skip the package.mo lookup entirely.
        source_type_hint = file_config.get("source_type", self.source_type)

        if source_type_hint == "modelica":
            # Resolve source path — CLI arg > config file > auto-detect
            if self.source_path is not None:
                self.source_path = Path(self.source_path).resolve()
                if not (self.source_path / "package.mo").exists():
                    self.source_path = find_package_dir(self.source_path)
            elif "source_path" in file_config:
                base_dir = config_found_dir or Path.cwd()
                self.source_path = (base_dir / file_config["source_path"]).resolve()
                if not (self.source_path / "package.mo").exists():
                    self.source_path = find_package_dir(self.source_path)
            else:
                self.source_path = find_package_dir()

            # Read library name from package.mo
            if self.library_name is None:
                self.library_name = read_package_name(self.source_path)

            # The parent of the package dir is where testing.json typically lives
            repo_root = self.source_path.parent
        else:
            # Non-modelica source: no package.mo to discover. The "library"
            # is conceptually the FMU set / data set / Julia project / etc.
            # described by the config. Resolve source_path here too so
            # backends that need it (JuliaRunner for --project=) find the
            # right directory. CLI arg > config file > config dir > cwd.
            if self.source_path is not None:
                self.source_path = Path(self.source_path).resolve()
            elif "source_path" in file_config:
                base_dir = config_found_dir or Path.cwd()
                self.source_path = (base_dir / file_config["source_path"]).resolve()
            elif config_found_dir is not None:
                self.source_path = config_found_dir
            repo_root = config_found_dir or (
                self.source_path if self.source_path else Path.cwd()
            )
            if self.library_name is None:
                self.library_name = (
                    file_config.get("library_name")
                    or (config_found_dir.name if config_found_dir else repo_root.name)
                )

        # If no config was found yet (source_path wasn't available for search),
        # try repo_root now
        if not file_config and config_found_dir is None:
            file_config = load_config_file(repo_root)
            if file_config:
                config_found_dir = repo_root.resolve()

        # Auto-create testing.json if none was found
        if not file_config:
            config_dir = self.reference_root if self.reference_root else repo_root
            file_config = _create_default_config(config_dir, self.library_name)

        # Source type (optional; default "modelica" from dataclass).
        # testing.json may set: "source_type": "fmu" | "julia" | ... (future)
        if "source_type" in file_config:
            self.source_type = file_config["source_type"]

        # OS detection
        if self.os_name is None:
            self.os_name = file_config.get("os", detect_os())

        # Simulator selection. Resolution order, first match wins:
        #   1. Explicit constructor / CLI value (self.simulator is not None)
        #   2. testing.json explicit "simulator" key
        #   3. testing.json "simulators" map auto-detect (first entry whose
        #      binary resolves on the current machine / PATH)
        #   4. Default "Dymola" (historical fallback)
        if self.simulator is None:
            if "simulator" in file_config:
                self.simulator = file_config["simulator"]
            else:
                simulators_config = file_config.get("simulators", {})
                detected = _auto_detect_simulator(simulators_config)
                if detected:
                    self.simulator, self.simulator_path = detected
                else:
                    self.simulator = "Dymola"

        # Show IDE
        if not self.show_ide and "show_ide" in file_config:
            self.show_ide = file_config["show_ide"]

        # Simulator setup commands
        if not self.simulator_setup and "simulator_setup" in file_config:
            self.simulator_setup = file_config["simulator_setup"]

        # Diagnostic variables
        if "diagnostic_variables" in file_config:
            self.diagnostic_variables = file_config["diagnostic_variables"]

        # Reporter payload budget
        if "max_embedded_samples" in file_config:
            self.max_embedded_samples = int(file_config["max_embedded_samples"])

        # User-provided recognizers (PTA.3) — declarative JSON specs become
        # Recognizer instances stored on Config. CLI-provided recognizers
        # take precedence; testing.json fills in if Config wasn't given any.
        if not self.recognizers and "recognizers" in file_config:
            from .discovery.json_recognizer import parse_recognizer_spec
            self.recognizers = [
                parse_recognizer_spec(spec) for spec in file_config["recognizers"]
            ]
        if not self.disabled_bundled and "disable_bundled" in file_config:
            self.disabled_bundled = list(file_config["disable_bundled"])

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
                backend = self.simulator_backend
                binary = BACKEND_BINARY_NAMES.get(backend, backend.lower())
                if binary:
                    self.simulator_path = shutil.which(binary) or binary

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
        """Path to the library's top-level package directory (contains package.mo).

        Modelica-source convenience accessor; for source_type == "modelica" this
        is identical to ``source_path``.
        """
        return self.source_path

    @property
    def reference_dir(self) -> Path:
        """Reference results directory, partitioned by simulator backend and OS."""
        return self.reference_root / self.simulator_backend / self.os_name

