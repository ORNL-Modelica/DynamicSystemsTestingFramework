"""Configuration constants and path resolution for the testing system."""

import json
import logging
import os
import platform
import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config_shape import ALL_POINTS, PROGRAM, TESTING_JSON, Setting, refused
from .config_shape import default as _default
from .json_types import holds, is_number, said

if TYPE_CHECKING:
    from .backends.locate import Located

logger = logging.getLogger(__name__)

# Comparison defaults: testing.json's `tolerance`, where it is not set.
DEFAULT_COMPARISON_TOLERANCE = _default("tolerance")

# Config filename looked for near the package or in working directory
CONFIG_FILENAME = "testing.json"


def detect_os() -> str:
    """Detect the current OS for reference result partitioning."""
    system = platform.system().lower()
    return {"linux": "linux", "darwin": "macos", "windows": "windows"}.get(
        system, system
    )


def _is_package(directory: Path) -> bool:
    """Whether ``directory`` holds a package.mo — and False where the
    system will not say: a directory that may not be looked into is not
    this project's library, and is no error either."""
    try:
        return (directory / "package.mo").exists()
    except OSError:
        return False


def find_package_dir(start: Path | None = None) -> Path:
    """The Modelica library a directory belongs to: the directory that
    holds its package.mo.

    ``start`` (the working directory by default) if it is a package, else
    the nearest directory above it that is one — a command run from a
    library's ``Resources`` folder is run in that library. Failing both, a
    package IN ``start``, the first by name: ``start`` is then the
    library's parent, where its testing.json is kept.

    Nothing else is looked at — not adopted, and not asked about. A
    library beside ``start``, or beside a directory above it, is another
    project's: taking it made an empty directory read as whichever library
    happened to be near, and `--source-path` of a directory with no
    package list the tests of the one beside it; and asking ended every
    command run below the system's temporary directory at a neighbour the
    system keeps closed (``Permission denied: '.../package.mo'``).
    """
    if start is None:
        start = Path.cwd()
    path = start.resolve()

    for directory in (path, *path.parents):
        if _is_package(directory):
            return directory
    for child in sorted(path.iterdir()):
        if _is_package(child):
            return child

    raise FileNotFoundError(
        f"Could not find a Modelica package from {start}. "
        "Expected package.mo in it, in a directory above it, or in a "
        "directory it holds."
    )


def read_package_name(package_dir: Path) -> str:
    """Read the Modelica package name from package.mo."""
    pkg_file = package_dir / "package.mo"
    if not pkg_file.exists():
        raise FileNotFoundError(f"No package.mo in {package_dir}")
    text = pkg_file.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"package\s+(\w+)", text)
    if m:
        return m.group(1)
    raise ValueError(f"Could not parse package name from {pkg_file}")


class ConfigError(ValueError):
    """A project's configuration cannot be used: ``testing.json`` or
    ``test_spec.json`` is malformed or holds a key nothing reads, a path is
    missing, a plug-in failed to load. ``str()`` names the file and the key.
    """


class NoProject(ConfigError):
    """There is no project here to configure: no testing.json was found and
    no library either. A command that can do without one (``dstf check``)
    catches this; to every other it is the config error it reads as."""


def baseline_points_refused(named: str, value: object) -> str | None:
    """Why ``value`` is no ``baseline_points`` — in ``testing.json`` or on a
    ``test_spec.json`` entry, ``named`` saying which — or ``None``. It is
    ``"all"`` or a whole number of 2 or more: a thinned baseline keeps both
    ends of its run. ``true`` is no number, and neither is ``2000.0``."""
    if isinstance(value, str) and value == ALL_POINTS:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 2:
        return None
    return (
        f"{named} is {json.dumps(value, default=str)}: the points a baseline "
        f'keeps are "{ALL_POINTS}" or a whole number of 2 or more'
    )


def json_from_bytes(raw: bytes, label: str) -> Any:
    """The JSON value a file's bytes hold, or :class:`ConfigError` naming
    ``label``: bytes that are no UTF-8 text, an empty file, text that is no
    JSON — or JSON no reader can hold, a number of thousands of digits or
    brackets nested by the ten thousand, which ``json`` refuses with errors
    of other kinds.

    THE one decoder of the two files a person writes: ``testing.json``
    (:func:`load_config_file`) and ``test_spec.json``
    (``spec_parser.spec_document_from_bytes``). A key written twice keeps
    its last value, as ``json`` reads it.

    ``utf-8-sig``: a file a Windows editor saved keeps a BOM, and a BOM is
    not a reason to refuse a file that otherwise parses."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ConfigError(f"{label}: not UTF-8 text ({e})") from e
    if not text.strip():
        raise ConfigError(f"{label}: the file is empty")
    try:
        return json.loads(text)
    except (ValueError, RecursionError) as e:
        raise ConfigError(f"{label}: not JSON ({e})") from e


def _resolved(path: Path) -> Path:
    """``path``, absolute and with its links followed — or
    :class:`ConfigError` naming it where the system cannot follow them (a
    link that leads back to itself)."""
    try:
        return path.resolve()
    except (OSError, RuntimeError) as e:  # RuntimeError: pathlib before 3.13
        raise ConfigError(f"{path}: {e}") from e


def load_config_file(path: Path) -> dict:
    """Load a testing.json config file.

    A missing file returns ``{}`` (no config there). An EXISTING file that
    cannot be read or decoded (:func:`json_from_bytes`) raises
    :class:`ConfigError` naming the file — review 2026-07-06 finding 11:
    silently returning ``{}`` let the auto-create branch overwrite the
    user's testing.json.

    So does one that holds a key nothing reads, or a value of another type
    than its key takes (``config_shape.TESTING_JSON``): ``"tolerence":
    0.01`` used to be skipped, and the project ran on the default. The
    ``backends`` blocks are checked where the backends are known
    (:func:`_backend_blocks`).
    """
    if path.is_dir():
        path = path / CONFIG_FILENAME
    if not path.exists():
        return {}
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise ConfigError(f"{path}: {e}") from e
    data = json_from_bytes(raw, str(path))
    if not isinstance(data, dict):
        raise ConfigError(
            f"{path}: must be an object, got {json.dumps(data, default=str)[:60]}"
        )
    problem = refused("", data, TESTING_JSON)
    if problem is not None:
        raise ConfigError(f"{path}: {problem}")
    if "source_type" in data:
        # One a backend runs — a plug-in's included. Any other used to be
        # read as "not Modelica": the .mo scan went off without a word.
        from .backends import source_types
        from .comparison.sides import _as_json

        choices = {"enum": source_types()}
        if not holds(choices, data["source_type"]):
            raise ConfigError(
                f"{path}: source_type: must be {said(choices)}, "
                f"got {_as_json(data['source_type'])}"
            )
    return data


def _default_file(project: dict) -> dict:
    """A default testing.json's contents: no backend named and none listed,
    so each machine runs with the one it has, and the ``project`` keys that
    name the project the command was given (`Config._project_keys`) —
    `--config <it>` is that project from any directory."""
    return {"backends": {}, "dependencies": [], **project}


def _create_default_config(config_dir: Path, project: dict) -> dict:
    """Write a default testing.json (:func:`_default_file`) in
    ``config_dir`` and return its contents."""
    config = _default_file(project)
    config_path = config_dir / CONFIG_FILENAME
    # review 2026-07-06 finding 19: the target dir may not exist yet (e.g.
    # first run with a fresh --reference-root) — create it before writing.
    config_dir.mkdir(parents=True, exist_ok=True)
    # LF on every platform: testing.json is a surface
    # users version-control, so it must not depend on the writing OS.
    config_path.write_text(
        json.dumps(config, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    logger.info("Created default %s at %s", CONFIG_FILENAME, config_path)
    print(f"Created default {CONFIG_FILENAME} at {config_path}")
    return config


def _backend_blocks(
    raw: object, label: str, base_dir: Path
) -> dict[str, dict[str, Any]]:
    """The ``backends`` map as ``Config`` keeps it: a settings block per
    entry, each checked against the table of the backend its entry names
    (``Backend.settings`` — an entry called ``"Dymola 2025"`` is a Dymola).
    ``label`` says where the map is written, for the :class:`ConfigError`.

    A setting that is a path (``Setting.path``) is kept as the absolute
    one it means: relative to ``base_dir``, the file's directory.

    The map held a list of executable paths per entry before a backend had
    settings of its own. Every entry still written so is refused in ONE
    sentence, with the block to write for each — a file of four is
    converted in one round."""
    from .backends import backend_class, backend_family, backend_names
    from .comparison.sides import _as_json

    if not isinstance(raw, dict):
        raise ConfigError(f"{label}: backends: must be an object, got {_as_json(raw)}")
    lists = {name: block for name, block in raw.items() if isinstance(block, list)}
    if lists:
        written = ", ".join(
            f"{json.dumps(name)}: "
            f"{json.dumps({'executables': paths} if paths else {}, default=str)}"
            for name, paths in lists.items()
        )
        raise ConfigError(
            f"{label}: {', '.join(f'backends.{name}' for name in lists)} must "
            f"{'each ' if len(lists) > 1 else ''}be an object (was a list of "
            f"paths) — write {written}"
        )
    blocks: dict[str, dict[str, Any]] = {}
    for name, block in raw.items():
        where = f"backends.{name}"
        if not isinstance(block, dict):
            raise ConfigError(
                f"{label}: {where}: must be an object, got {_as_json(block)}"
            )
        cls = backend_class(backend_family(name))
        if cls is None:
            raise ConfigError(
                f"{label}: {where}: no backend is registered under that name; "
                f"the backends are: {', '.join(backend_names())}"
            )
        problem = refused(where, block, cls.settings)
        if problem is not None:
            raise ConfigError(f"{label}: {problem}")
        blocks[name] = {
            key: _anchored(value, cls.settings[key], base_dir)
            for key, value in block.items()
        }
    return blocks


def _anchored(value: Any, setting: Setting, base_dir: Path) -> Any:
    """A block's value as ``Config`` keeps it: a path setting's
    (``Setting.path``) made absolute against ``base_dir``, a program's name
    left for ``PATH``; any other value as it is."""
    from .backends.locate import anchored, looks_like_path

    if not setting.path or value is None:
        return value

    def one(entry: str) -> str:
        if setting.path == PROGRAM and not looks_like_path(entry):
            return entry
        return anchored(entry if looks_like_path(entry) else f"./{entry}", base_dir)

    return [one(entry) for entry in value] if isinstance(value, list) else one(value)


def _pick_backend(
    backends: dict[str, dict[str, Any]], source_type: str
) -> tuple[str, str | None]:
    """The backend of a project that names none, and the line that says
    which entries of ``backends`` before it were passed over and why — None
    where none was.

    With a ``backends`` map: its first entry, in the order written, that is
    found on this machine (``Backend.locate``) — so one ``testing.json``
    lists Dymola first for the Windows machine and OpenModelica for the
    Linux one, and each runs what it has. Without one: the same over the
    registered backends that run this ``source_type``
    (``Backend.source_types``), in registration order.

    Where none is found, the FIRST candidate: the run then says that this
    one is missing, and where it was looked for. Never a backend the
    project did not list.

    The line is the map's only: a written entry that is not here is said
    (`Python not found (… is a directory) — using Python 2`); a backend the
    search alone did not find is not news.
    """
    from .backends import backend_class, backend_family, backend_names

    candidates: list[tuple[str, dict[str, Any]]] = list(backends.items())
    if not candidates:
        for name in backend_names():
            cls = backend_class(name)
            if cls is not None and source_type in cls.source_types:
                candidates.append((name, {}))
    if not candidates:
        raise ConfigError(
            f'no backend runs source_type "{source_type}": name one with "backend" '
            f"(the backends are: {', '.join(backend_names())})"
        )
    skipped: list[str] = []
    for name, block in candidates:
        cls = backend_class(backend_family(name))
        if cls is None:
            continue
        located = cls.locate(block)
        if located.found:
            said = "; ".join(skipped) if backends and skipped else None
            return name, (f"{said} — using {name}" if said else None)
        detail = located.parts[0].detail if located.parts else ""
        skipped.append(
            f"{name} not found ({detail})" if detail else f"{name} not found"
        )
    return candidates[0][0], None


def dependency_is_path(entry: str) -> bool:
    """True if a ``dependencies`` entry is SPELLED as a filesystem path: a
    separator, a ``.mo`` suffix, or a ``.``/``~`` prefix. Every entry of a
    resolved ``Config.dependencies`` that names a path is (a relative one
    was made absolute); a bare library name ("Modelica") is not — the
    backend loads it by name."""
    return (
        "/" in entry
        or "\\" in entry
        or entry.endswith(".mo")
        or entry.startswith((".", "~"))
    )


def _dependency_looks_like_path(entry: str, base_dir: Path) -> bool:
    """True if a ``dependencies`` entry denotes a filesystem path.

    review 2026-07-06 finding 17: bare library names ("Modelica") must NOT
    be absolutized — backends load them by name. Path signals: how it is
    spelled (:func:`dependency_is_path`), or an existing file/dir relative
    to the config file's directory.
    """
    if dependency_is_path(entry):
        return True
    try:
        return (base_dir / entry).exists()
    except OSError:
        return False


def _must_exist(path: Path, named: str) -> None:
    """A library path that is not there is said as that, in one sentence —
    not as the OSError the package search would meet."""
    if not path.exists():
        raise ConfigError(f"{named} {path}: no such directory")


#: The Config fields a caller may give over its testing.json and the
#: defaults (`Config.overrides`): the places of the project, then the
#: settings that change what a run does to it.
_OVERRIDABLE = (
    "source_path",
    "reference_root",
    "test_spec_file",
    "work_dir",
    "backend_name",
    "backend_path",
    "tolerance",
    "timeout",
    "default_points",
)


@dataclass
class Config:
    """A project's configuration: its ``testing.json``, the command line's
    flags over it, and the defaults, with every path made absolute.

    Build one with the file, ``Config(config_file=Path("testing.json"))``; a
    keyword given here wins over the file, as the flag of the same name does on
    the command line. Plug-ins load first (:func:`dstf.load_plugins`): a
    backend's settings are checked against its table.

    Attributes:
        config_file: The ``testing.json`` to read, or the directory holding
            it; None to look for one near the library.
        source_path: The library under test.
        reference_root: Where the stored baselines are.
        test_spec_file: The ``test_spec.json``.
        work_dir: Where a run writes.
        backend_name: The backend's entry or name; None for the file's choice.
        parallel: How many tests run at once.
        timeout: Seconds a test may run.
        tolerance: The project's base tolerance.
        backend_settings: The backend's settings block, after construction.

    Raises:
        ConfigError: The file or a value cannot be used; the message names the
            key.
    """

    # Source type: "modelica" (default, `source_path` points at a package.mo dir),
    # "fmu" (Phase 2), "julia" / "simulink" / "data-file" (future). See docs/vision.md.
    # Discovery + backend selection gate on this.
    source_type: str = field(default_factory=lambda: _default("source_type"))

    # Path to the source location for the library being tested.
    # For source_type == "modelica": the directory containing package.mo.
    # For other source types: the FMU directory / Julia script / CSV file / etc.
    source_path: Path | None = None

    # Reference results location (can be a separate repo/directory)
    reference_root: Path | None = None

    # Backend selection. ``None`` means "not explicitly chosen" — the
    # post-init resolution then consults testing.json's ``backend`` key,
    # then the first entry of the ``backends`` map that is found on this
    # machine, then the installed backends that run this source type
    # (``_pick_backend``). A non-None value (typically
    # from a CLI ``--backend`` flag) is treated as authoritative and is
    # NOT overridden by anything from testing.json.
    backend_name: str | None = None
    # The backend's executable. Given (CLI ``--backend-path``), it is the
    # one executable the backend's ``locate`` looks at. After construction:
    # what ``locate`` found, or None — not on this machine (``located``
    # says where it was looked for), the given one included.
    backend_path: str | None = None
    backend_setup: list[str] = field(
        default_factory=lambda: _default("backend_setup")
    )  # Commands run after loading libraries

    # OS override (auto-detected if not set)
    os_name: str | None = None

    # Paths to dependency library roots
    dependencies: list[str] = field(default_factory=lambda: _default("dependencies"))

    # Simulation / comparison
    parallel: int = 1
    # Global comparison tolerance. None means "not explicitly set" — resolved
    # in __post_init__ as CLI/constructor arg > testing.json `tolerance` key >
    # DEFAULT_COMPARISON_TOLERANCE, with is-not-None semantics so an explicit
    # 0.0 is honored (review 2026-07-06 finding 12). Always a float after
    # construction.
    tolerance: float | None = None
    default_points: bool = False
    timeout: int = 60
    # Seconds a persistent worker's start may take (testing.json
    # `worker_startup_timeout`). None means "not set": the worker's own
    # bound applies (`Worker.startup_timeout`, which is that key's default).
    worker_startup_timeout: float | None = None

    # The run's results will be read WHILE it is in progress (the authoring
    # tool showing a running test). Set by that caller only: there is no
    # testing.json key and no CLI flag, so a `dstf run` is never changed by
    # it. A backend whose result file would otherwise reach the disk late
    # pays to write it promptly — OpenModelica passes `-mat_sync=1`, measured
    # at +3 % run time on a local disk and +59 % for an output-heavy run on a
    # Windows mount. Dymola appends as it goes and needs nothing.
    live_results: bool = False

    # Output
    work_dir: Path | None = None

    # Test spec file (external test definitions)
    test_spec_file: Path | None = None

    # Diagnostic variables: auto-captured from simulation, shown in reports but not compared
    diagnostic_variables: list[str] = field(
        default_factory=lambda: _default("diagnostic_variables")
    )

    # Phase 6.0 — interactive.html payload budget. LTTB-decimates trajectories
    # embedded for Plotly rendering; full-resolution arrays remain on disk in
    # comparison_data.json. Only affects the HTML visual; pass/fail scoring,
    # stored baselines, and the data sidecar are unaffected. Default 1000 keeps
    # a 50-var × 5000-sample test under the ~5 MB budget given today's
    # per-variable time-array duplication; the follow-up dedup (idea #47)
    # will let this rise to 2000 at the same budget.
    max_embedded_samples: int = field(
        default_factory=lambda: _default("max_embedded_samples")
    )

    # The points a stored baseline keeps of its run (D153, testing.json
    # `baseline_points`): "all" — every row the simulator wrote — or a whole
    # number of 2 or more, which thins the run to at most that many. The
    # project's value: a test_spec.json entry's own `baseline_points` wins
    # (`TestModel.baseline_points`).
    # Scoring reads the run AT the baseline's time points, so this is also
    # the resolution at which a regression can be seen.
    baseline_points: int | str = field(
        default_factory=lambda: _default("baseline_points")
    )

    # The bundled theme (D148) the dashboard and the report pages are
    # generated in and `dstf author` opens with: a NAME of
    # reporting.theme.THEMES — no path, no inline colours, no CLI flag.
    theme: str = field(default_factory=lambda: _default("theme"))

    # User-provided recognizers (PTA.3) — parsed from testing.json's
    # "recognizers" list via discovery.json_recognizer.parse_recognizer_spec.
    # Stored on Config so they're scoped to this run (no module-registry leak
    # across multiple Config instances or between tests).
    recognizers: list = field(default_factory=list)

    # Names of bundled recognizers to disable (PTA.3). E.g.,
    # ["modelica:bundled-unit-tests"] when the user has only custom-convention
    # tests and wants the bundled UnitTests recognizer off.
    disabled_bundled: list[str] = field(default_factory=list)

    # testing.json's `backends` map: a settings block per entry, each
    # checked against the table of the backend its entry names
    # (`_backend_blocks`). The authoring shell's backend select lists its
    # entries; the backend's install is located from the selected one.
    backends: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Config file path
    config_file: Path | None = None

    # Whether a project with no testing.json anywhere gets a default one
    # written. Not unless the caller says so: a command says it in the
    # command tree (`cli.parser`, `creates=True`) when it may create a file
    # of the project; one that reads, lists, exports or removes does not.
    # The Config reads it as written at once; the file is written by
    # `write_default_file`, when the command is past its own checks.
    write_default: bool = False

    # Resolved (set during __post_init__)
    library_name: str | None = None

    # The testing.json that was read — the one given, the one the search
    # found, or the default one this command wrote — or None: there is
    # none, and the project runs on defaults.
    config_found: Path | None = field(init=False, default=None)

    # What this Config did NOT take from its testing.json and the defaults:
    # the fields of _OVERRIDABLE the caller gave (a value other than the
    # field's default), and the places derived from the current directory
    # (a package found from it, the default work dir under it). A command
    # that is to do the same thing to the same project from another
    # directory names each beside `--config` — the dashboard's commands
    # (`reporting.dashboard_render.build_rerun_flags`).
    overrides: frozenset[str] = field(init=False, default=frozenset())

    # The default testing.json this Config reads but has not written yet:
    # its directory and its project keys (`write_default_file`).
    _unwritten: tuple[Path, dict] | None = field(init=False, default=None, repr=False)

    # The entries of `backends` before the selected one that were not found
    # here, and why, as one line (`_pick_backend`) — None where the project
    # named its backend or the first entry was found. `run` and `check` say it.
    passed_over: str | None = field(init=False, default=None)

    # The selected backend's block of `backends`, as written (empty: it has
    # none). `Backend.settings_of` lays it over the backend's defaults.
    backend_settings: dict[str, Any] = field(init=False, default_factory=dict)
    # Where the selected backend's install is and by which rule each part
    # was found (`Backend.locate`). Set by __post_init__; None only on a
    # stand-in that never went through it.
    located: "Located | None" = field(init=False, default=None)

    def __post_init__(self):
        defaults = {f.name: f.default for f in fields(self)}
        overrides = {
            name for name in _OVERRIDABLE if getattr(self, name) != defaults[name]
        }
        # Resolve reference root early — needed for config file search
        if self.reference_root is not None:
            self.reference_root = _resolved(Path(self.reference_root))

        # Load config file first — it may provide source_path
        file_config = {}
        config_found_dir = None
        # The file that was read, for an error to name.
        config_found: Path | None = None
        if self.config_file:
            config_path = _resolved(Path(self.config_file))
            if config_path.is_dir():
                config_path = config_path / CONFIG_FILENAME
            if not config_path.is_file():
                # review 2026-07-06 finding 14: an explicit --config that
                # doesn't exist must fail loudly, not silently fall through
                # to auto-detection (and a freshly written default config).
                raise ConfigError(f"Config file not found: {config_path}")
            config_found_dir = config_path.parent
            config_found = config_path
            file_config = load_config_file(config_path)
        else:
            # Build search dirs from what we know so far
            search_dirs = [Path.cwd()]
            if self.reference_root is not None:
                search_dirs.insert(0, self.reference_root)
            if self.source_path is not None:
                pkg = _resolved(Path(self.source_path))
                search_dirs.insert(0, pkg)  # package dir
                search_dirs.insert(0, pkg.parent)  # repo root
            for search_dir in search_dirs:
                # review 2026-07-06 findings 11/19: "found" means the FILE
                # exists, not that it parsed to a non-empty dict — an existing
                # (even empty) testing.json must stop the search and suppress
                # the auto-create-defaults branch.
                if (search_dir / CONFIG_FILENAME).is_file():
                    file_config = load_config_file(search_dir)
                    config_found_dir = _resolved(search_dir)
                    config_found = config_found_dir / CONFIG_FILENAME
                    break

        # Source type peek — controls whether we look for a Modelica package.
        # Done before source_path resolution so non-modelica backends (FMU,
        # data-file, ...) can skip the package.mo lookup entirely.
        source_type_hint = file_config.get("source_type", self.source_type)

        if source_type_hint == "modelica":
            # Resolve source path — CLI arg > config file > auto-detect.
            # "no package.mo anywhere" is a user error (wrong --source-path,
            # wrong cwd, or source_type should have been "fmu"/"julia"), so
            # find_package_dir's FileNotFoundError becomes a ConfigError and
            # main() prints one line instead of a traceback.
            try:
                if self.source_path is not None:
                    self.source_path = _resolved(Path(self.source_path))
                    _must_exist(self.source_path, "--source-path")
                    if not (self.source_path / "package.mo").exists():
                        self.source_path = find_package_dir(self.source_path)
                elif "source_path" in file_config:
                    base_dir = config_found_dir or Path.cwd()
                    self.source_path = _resolved(base_dir / file_config["source_path"])
                    _must_exist(self.source_path, f"{config_found}: source_path")
                    if not (self.source_path / "package.mo").exists():
                        self.source_path = find_package_dir(self.source_path)
                else:
                    self.source_path = find_package_dir()
                    overrides.add("source_path")
            except OSError as e:
                # Nothing was named and nothing was found: no project here.
                nothing = config_found_dir is None and self.source_path is None
                raise (NoProject if nothing else ConfigError)(
                    f"{e} Set 'source_path' in testing.json, pass --source-path, "
                    f"or set 'source_type' if this library isn't Modelica."
                ) from e

            # Read library name from package.mo
            if self.library_name is None:
                try:
                    self.library_name = read_package_name(self.source_path)
                except (FileNotFoundError, ValueError) as e:
                    raise ConfigError(str(e)) from e

            # The parent of the package dir is where testing.json typically lives
            repo_root = self.source_path.parent
        else:
            # Non-modelica source: no package.mo to discover. The "library"
            # is conceptually the FMU set / data set / Julia project / etc.
            # described by the config. Resolve source_path here too so
            # backends that need it (JuliaBackend for --project=) find the
            # right directory. CLI arg > config file > config dir > cwd.
            if self.source_path is not None:
                self.source_path = _resolved(Path(self.source_path))
            elif "source_path" in file_config:
                base_dir = config_found_dir or Path.cwd()
                self.source_path = _resolved(base_dir / file_config["source_path"])
            elif config_found_dir is not None:
                self.source_path = config_found_dir
            repo_root = config_found_dir or (
                self.source_path if self.source_path else Path.cwd()
            )
            if self.library_name is None:
                self.library_name = file_config.get("library_name") or (
                    config_found_dir.name if config_found_dir else repo_root.name
                )

        # If no config was found yet (source_path wasn't available for search),
        # try repo_root now — existence-based, same as the search loop above.
        if config_found_dir is None and (repo_root / CONFIG_FILENAME).is_file():
            file_config = load_config_file(repo_root)
            config_found_dir = _resolved(repo_root)
            config_found = config_found_dir / CONFIG_FILENAME

        # Auto-create testing.json ONLY if no config file was found anywhere
        # (review 2026-07-06 findings 11/19: a found config — even one that
        # parses to {} — must never be overwritten with defaults).
        # It is written by `write_default_file`, which the command calls once
        # it is past its own checks: a command that fails at them leaves no
        # file behind. Until then the Config reads it as written.
        if config_found_dir is None and self.write_default:
            config_dir = _resolved(
                self.reference_root if self.reference_root else repo_root
            )
            project = self._project_keys(config_dir)
            self._unwritten = (config_dir, project)
            file_config = _default_file(project)
            # From here on the file is read as any found one is: what it
            # says is what `--config <it>` says from any directory.
            config_found_dir = config_dir
            config_found = config_dir / CONFIG_FILENAME

        # Source type (optional; default "modelica" from dataclass).
        # testing.json may set: "source_type": "fmu" | "julia" | ... (future)
        if "source_type" in file_config:
            self.source_type = file_config["source_type"]

        # OS detection
        if self.os_name is None:
            self.os_name = file_config.get("os", detect_os())

        self.config_found = config_found

        # The `backends` map, each block checked against its backend's own
        # settings. A map given to the constructor stands for the file's.
        from .backends import backend_class, backend_names
        from .backends.locate import anchored, with_backend_path

        self.backends = _backend_blocks(
            self.backends or file_config.get("backends", {}),
            str(config_found) if config_found and not self.backends else "Config",
            config_found_dir or Path.cwd(),
        )

        # Backend selection. Resolution order, first match wins:
        #   1. Explicit constructor / CLI value (self.backend_name is not None)
        #   2. testing.json explicit "backend" key
        #   3. The first entry of the "backends" map that is found on this
        #      machine; with no map, the first registered backend that runs
        #      this source_type and is found (`_pick_backend`)
        named = "--backend"
        if self.backend_name is None:
            if "backend" in file_config:
                self.backend_name = file_config["backend"]
                named = f"{config_found}: backend"
            else:
                self.backend_name, self.passed_over = _pick_backend(
                    self.backends, self.source_type
                )

        # A name that is an entry of `backends`, a backend's, or one that
        # starts with a backend's ("Dymola 2025"). Any other names nothing
        # that could run, and no partition a baseline could be read from.
        backend_cls = backend_class(self.backend)
        if backend_cls is None:
            entries = f" ({', '.join(self.backends)})" if self.backends else ""
            raise ConfigError(
                f"{named}: {json.dumps(self.backend_name)} is no entry of "
                f"backends{entries} and no backend's name "
                f"({', '.join(backend_names())})"
            )

        # The selected backend's settings, and where its install is: the
        # backend's own `locate` is the one place that is looked for.
        self.backend_settings = dict(self.backends.get(self.backend_name, {}))
        block = self.backend_settings
        if self.backend_path is not None:
            # The one executable looked at, by the rule a block's pinned
            # entry goes by: one that is not there is not found, and no
            # other install is taken in its place.
            block = with_backend_path(block, anchored(self.backend_path, Path.cwd()))
        self.located = backend_cls.locate(block)
        self.backend_path = self.located.executable

        # Backend setup commands
        if not self.backend_setup and "backend_setup" in file_config:
            self.backend_setup = file_config["backend_setup"]

        # Diagnostic variables
        if "diagnostic_variables" in file_config:
            self.diagnostic_variables = file_config["diagnostic_variables"]

        # Global comparison tolerance (review 2026-07-06 finding 12: the
        # documented testing.json `tolerance` key was never read). Precedence:
        # explicit constructor/CLI value > file > default, is-not-None
        # semantics so an explicit 0.0 anywhere is honored.
        # The project's base tolerance, as a test's: ONE finite number
        # (`is_base_tolerance`). Anything else is this config error, not a
        # TypeError/OverflowError traceback — nor `"0.5"` or `true` read as
        # a number by `float()`. `null` is unset.
        from .comparison.sides import base_tolerance_refused, is_base_tolerance

        if self.tolerance is None:
            tolerance, named = (
                file_config.get("tolerance"),
                f"{config_found}: tolerance",
            )
        else:
            tolerance, named = self.tolerance, "--tolerance"
        if tolerance is None:
            self.tolerance = _default("tolerance")
        elif is_base_tolerance(tolerance):
            self.tolerance = float(tolerance)
        else:
            raise ConfigError(base_tolerance_refused(named, tolerance))

        # A worker start's bound: one positive finite number of seconds.
        startup = file_config.get("worker_startup_timeout")
        if startup is not None:
            if not is_number(startup) or not startup > 0:
                raise ConfigError(
                    f"{config_found}: worker_startup_timeout must be a positive "
                    f"number of seconds, got {startup!r}"
                )
            self.worker_startup_timeout = float(startup)

        # Reporter payload budget
        if "max_embedded_samples" in file_config:
            self.max_embedded_samples = int(file_config["max_embedded_samples"])

        # A baseline's points: "all" or a whole number of 2 or more,
        # wherever it was said. `null` is no value of it: absent is the
        # only way to leave it unset.
        if "baseline_points" in file_config:
            self.baseline_points = file_config["baseline_points"]
            named = f"{config_found}: baseline_points"
        else:
            named = "baseline_points"
        problem = baseline_points_refused(named, self.baseline_points)
        if problem is not None:
            raise ConfigError(problem)

        # The theme: one of the bundled names, wherever it was said.
        from .reporting.theme import THEMES

        if "theme" in file_config:
            self.theme, named = file_config["theme"], f"{config_found}: theme"
        else:
            named = "theme"
        if not isinstance(self.theme, str) or self.theme not in THEMES:
            raise ConfigError(
                f"{named} is {json.dumps(self.theme, default=str)}: the bundled themes are "
                f"{', '.join(THEMES)}"
            )

        # User-provided recognizers (PTA.3) — declarative JSON specs become
        # Recognizer instances stored on Config. CLI-provided recognizers
        # take precedence; testing.json fills in if Config wasn't given any.
        if not self.recognizers and "recognizers" in file_config:
            from .comparison.sides import _as_json
            from .discovery.json_recognizer import (
                RecognizerSpecError,
                parse_recognizer_spec,
            )

            entries = file_config["recognizers"]
            if not isinstance(entries, list):
                raise ConfigError(
                    f"{config_found}: recognizers: must be a list, got {_as_json(entries)}"
                )
            try:
                self.recognizers = [
                    parse_recognizer_spec(spec, f"recognizers[{i}]")
                    for i, spec in enumerate(entries)
                ]
            except RecognizerSpecError as e:
                # A malformed recognizer is a testing.json problem the user
                # must fix — one clean line and exit 2, not a traceback.
                raise ConfigError(f"{config_found}: {e}") from e
        if not self.disabled_bundled and "disable_bundled" in file_config:
            self.disabled_bundled = list(file_config["disable_bundled"])

        # Reference root (may already be set from CLI arg)
        if self.reference_root is None:
            ref_path = file_config.get("reference_root")
            if ref_path:
                base_dir = config_found_dir or repo_root
                self.reference_root = _resolved(base_dir / ref_path)
            else:
                # Default: if testing.json lives in a ReferenceResults dir, use that;
                # otherwise fall back to <repo>/Resources/ReferenceResults
                if config_found_dir and config_found_dir.name == "ReferenceResults":
                    self.reference_root = config_found_dir
                else:
                    self.reference_root = repo_root / "Resources" / "ReferenceResults"

        # Dependencies — resolve relative paths from config file location.
        # review 2026-07-06 finding 17: only absolutize entries that LOOK
        # like paths. Bare library names (the documented
        # ``"dependencies": ["Modelica"]`` form) must pass through untouched
        # so the OpenModelica backend's classify_dependency routes them to
        # ``loadModel(Modelica)`` instead of ``loadFile(<bogus path>)`` —
        # absolutizing them also suppressed the MSL auto-injection.
        # Dymola note: its backends load dependencies via
        # ``openModel(<dir>/package.mo)`` and cannot load by bare name, but
        # Dymola auto-loads MSL natively, so "Modelica" should simply be
        # omitted there; a bare name reaching the Dymola backend fails at
        # startup with a clear message, exactly as the absolutized bogus
        # path did before this fix (no regression).
        if not self.dependencies and "dependencies" in file_config:
            base_dir = config_found_dir or repo_root
            resolved_deps = []
            for dep in file_config["dependencies"]:
                if not _dependency_looks_like_path(dep, base_dir):
                    resolved_deps.append(dep)  # bare library name
                    continue
                dep_path = Path(dep)
                if not dep_path.is_absolute():
                    dep_path = _resolved(base_dir / dep_path)
                resolved_deps.append(str(dep_path))
            self.dependencies = resolved_deps

        # Test spec file — resolve relative to where testing.json was found
        if self.test_spec_file is None:
            spec_path = file_config.get("test_spec")
            if spec_path:
                base_dir = config_found_dir or repo_root
                self.test_spec_file = _resolved(base_dir / spec_path)
        else:
            self.test_spec_file = _resolved(Path(self.test_spec_file))

        # Work directory
        if self.work_dir is None:
            work = file_config.get("work_dir")
            if work:
                work_path = Path(work)
                if not work_path.is_absolute():
                    # review 2026-07-06 finding 15: a relative work_dir
                    # resolves against the config file's directory (like
                    # test_spec / dependencies), not the CWD — `compare`
                    # from another directory must find the same results.
                    work_path = (config_found_dir or repo_root) / work_path
                self.work_dir = _resolved(work_path)
            else:
                backend_dir = self.backend_name.replace(" ", "_")
                self.work_dir = (
                    Path.cwd()
                    / "testing_output"
                    / self.library_name
                    / backend_dir
                    / self.os_name
                )
                overrides.add("work_dir")
        else:
            self.work_dir = _resolved(Path(self.work_dir))
        self.overrides = frozenset(overrides)

    def write_default_file(self) -> None:
        """Write the default testing.json this Config was built to write
        (``write_default``, none found); nothing when there is none to
        write or it was written. A command calls it once it is past its own
        checks — before the first file it writes that names the project
        (the dashboard's commands name it), or once its work is done."""
        if self._unwritten is not None:
            config_dir, project = self._unwritten
            _create_default_config(config_dir, project)
            self._unwritten = None

    def _project_keys(self, config_dir: Path) -> dict:
        """What a default testing.json written in ``config_dir`` records so
        that it alone names this project from any directory: the library,
        and the source type, library name, reference root and spec where
        the file would not default to them. A key that would restate its
        default is left out."""

        def rel(path: Path) -> str:
            try:
                return Path(os.path.relpath(path, config_dir)).as_posix()
            except ValueError:  # another drive: no relative path reaches it
                return str(path)

        keys: dict = {}
        modelica = self.source_type == "modelica"
        if self.source_type != _default("source_type"):
            keys["source_type"] = self.source_type
        # A Modelica library without the key is looked for from the cwd;
        # any other source is the file's directory.
        if self.source_path is not None and (
            modelica or self.source_path != config_dir
        ):
            keys["source_path"] = rel(self.source_path)
        if not modelica and self.library_name != config_dir.name:
            keys["library_name"] = self.library_name
        repo_root = (
            self.source_path.parent if modelica and self.source_path else config_dir
        )
        default_root = (
            config_dir
            if config_dir.name == "ReferenceResults"
            else repo_root / "Resources" / "ReferenceResults"
        )
        root = self.reference_root or default_root
        if root != default_root:
            keys["reference_root"] = rel(root)
        if self.test_spec_file is not None:
            spec = _resolved(Path(self.test_spec_file))
            if spec != root / "test_spec.json":
                keys["test_spec"] = rel(spec)
        return keys

    @property
    def backend(self) -> str:
        """The canonical backend family (e.g., 'Dymola' from 'Dymola 2025'):
        the registered backend the name starts with."""
        from .backends import backend_family

        assert self.backend_name is not None  # resolved in __post_init__
        return backend_family(self.backend_name)

    @property
    def library_dir(self) -> Path:
        """Path to the library's top-level package directory (contains package.mo).

        Modelica-source convenience accessor; for source_type == "modelica" this
        is identical to ``source_path``.
        """
        assert self.source_path is not None  # resolved in __post_init__
        return self.source_path

    @property
    def reference_dir(self) -> Path:
        """Reference results directory, partitioned by backend and OS."""
        assert self.reference_root is not None  # resolved in __post_init__
        assert self.os_name is not None  # resolved in __post_init__
        return self.reference_root / self.backend / self.os_name

    @property
    def spec_path(self) -> Path:
        """Where ``test_spec.json`` lives: the configured file, else
        ``<reference_root>/test_spec.json``. The one spelling of a fallback
        that ``spec add``, ``spec update`` and the review loop each used to
        carry; the authoring Session reads the same property."""
        if self.test_spec_file is not None:
            return self.test_spec_file
        assert self.reference_root is not None  # resolved in __post_init__
        return self.reference_root / "test_spec.json"
