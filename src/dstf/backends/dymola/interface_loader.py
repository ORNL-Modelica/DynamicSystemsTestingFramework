"""Locate and load Dymola's Python interface (DymolaInterface).

Dymola ships its Python integration under
  <Dymola>/Modelica/Library/python_interface/
as either `dymola.egg` (older) or `dymola-<version>-py3-none-any.whl` (newer).

Both are zip archives. `.egg` is directly importable via Python's zipimport by
appending to `sys.path`. `.whl` is not — Python only imports `.whl` content
after `pip install` extracts it. To avoid a per-Dymola-version install step,
we extract the wheel once to a user cache dir and add that dir to `sys.path`.

Where the archive is looked for (:func:`find_interface`, a part of
``DymolaBackend.locate``), the first that names a place winning:
  1. the Dymola block's `interface` setting in testing.json
  2. the `DYMOLA_INTERFACE_PATH` environment variable
  3. beside the Dymola executable that was found
  4. the standard install folders, the newest Dymola first

A setting or a variable may name either the archive itself or the directory
containing it (we look inside). One that holds no archive is NOT FOUND:
another Dymola's interface is not taken in its place.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..locate import ENVIRONMENT, SEARCH, SETTING, Found, Rule

#: What the archive is called in a ``Located``.
INTERFACE = "Python interface"

#: The machine-level override of where the archive is.
INTERFACE_VARIABLE = "DYMOLA_INTERFACE_PATH"


def dymola_sort_key(path: Path) -> tuple:
    """Sort Dymola install paths so newer versions come first.

    Extracts the year/refresh from names like 'Dymola 2026x',
    'Dymola 2024x Refresh 1' or 'dymola-2026x-x86_64' and sorts descending.
    """
    name = str(path)
    m = re.search(
        r"Dymola[\s_-]?(\d{4})x(?:[\s_]+Refresh[\s_]*(\d+))?", name, re.IGNORECASE
    )
    if not m:
        return (0, 0, name)
    year = int(m.group(1))
    refresh = int(m.group(2)) if m.group(2) else 0
    # Negate for descending sort
    return (-year, -refresh, name)


def platform_glob_bases() -> list[Path]:
    """Base directories to glob for installed Dymola versions."""
    if sys.platform.startswith("win"):
        candidates = [
            Path(r"C:\Program Files"),
            Path(r"C:\Program Files (x86)"),
        ]
    elif sys.platform == "darwin":
        candidates = [Path("/Applications")]
    else:
        candidates = [Path("/opt"), Path("/usr/local")]
    return [p for p in candidates if p.exists()]


def executable_glob() -> str:
    """Below a base directory, the program a Dymola install is started by.
    On Linux that is the install's wrapper script, which sets the library
    path its bare `bin64/dymola` needs."""
    name = "Dymola.exe" if sys.platform.startswith("win") else "dymola.sh"
    return f"[Dd]ymola*/bin64/{name}"


def _find_python_interface_dirs() -> list[Path]:
    """Return all known python_interface directories, newest-first."""
    found: list[Path] = []
    for base in platform_glob_bases():
        for p in base.glob("[Dd]ymola*/Modelica/Library/python_interface"):
            if p.is_dir():
                found.append(p)
    found.sort(key=dymola_sort_key)
    return found


def _archive_in_dir(d: Path) -> Path | None:
    """Find a dymola .whl or .egg inside a python_interface directory."""
    wheels = sorted(d.glob("dymola-*.whl"), reverse=True)
    if wheels:
        return wheels[0]
    egg = d / "dymola.egg"
    if egg.exists():
        return egg
    eggs = sorted(d.glob("dymola*.egg"), reverse=True)
    if eggs:
        return eggs[0]
    return None


def _archive_at(named: Path) -> Path | None:
    """The archive a setting or a variable names: the file itself, or the
    one in the directory it names."""
    if named.is_file() and named.suffix in (".whl", ".egg"):
        return named
    if named.is_dir():
        return _archive_in_dir(named)
    return None


#: The line of a launcher script that says where its Dymola is installed.
_LAUNCHER_INSTALL_RE = re.compile(
    r"""^\s*(?:export\s+)?DYMOLA=(?:"([^"]+)"|'([^']+)'|(\S+))\s*$""", re.MULTILINE
)


def install_of(executable: Path) -> Path | None:
    """The Dymola install an executable belongs to, or None.

    Two shapes are known. The program inside the install
    (`<install>/bin64/Dymola.exe`, `<install>/bin64/dymola.sh`), reached
    through any symlinks: the install is the directory above that holds
    `Modelica/Library`. And Linux's launcher outside it
    (`/usr/local/bin/dymola` → `dymola-<version>`): a few lines of shell
    that say `export DYMOLA=<install>` before they start the program.
    """
    try:
        real = executable.resolve()
    except OSError:
        return None
    for parent in list(real.parents)[:3]:
        if (parent / "Modelica" / "Library").is_dir():
            return parent
    try:
        if not real.is_file() or real.stat().st_size > 4096:
            return None
        text = real.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = _LAUNCHER_INSTALL_RE.search(text)
    if m is None:
        return None
    install = Path(next(group for group in m.groups() if group is not None))
    return install if (install / "Modelica" / "Library").is_dir() else None


def find_interface(settings: Mapping[str, Any], executable: str | None) -> Found:
    """Dymola's Python interface archive, by the rules in this module's
    docstring. ``executable`` is the Dymola that was found, or None."""
    named = settings.get("interface")
    if named:
        archive = _archive_at(Path(named).expanduser())
        if archive is None:
            return Found(
                INTERFACE,
                detail=f"the configured interface holds no dymola-*.whl or dymola.egg: {named}",
            )
        return Found(INTERFACE, str(archive), Rule(SETTING, "interface"))
    named = os.environ.get(INTERFACE_VARIABLE)
    if named:
        archive = _archive_at(Path(named).expanduser())
        if archive is None:
            return Found(
                INTERFACE,
                detail=f"{INTERFACE_VARIABLE} holds no dymola-*.whl or dymola.egg: {named}",
            )
        return Found(INTERFACE, str(archive), Rule(ENVIRONMENT, INTERFACE_VARIABLE))
    if executable:
        install = install_of(Path(executable))
        if install is not None:
            archive = _archive_in_dir(
                install / "Modelica" / "Library" / "python_interface"
            )
            if archive is not None:
                return Found(
                    INTERFACE, str(archive), Rule(SEARCH, "beside the executable")
                )
    for d in _find_python_interface_dirs():
        archive = _archive_in_dir(d)
        if archive is not None:
            base = next(b for b in platform_glob_bases() if b in d.parents)
            return Found(
                INTERFACE, str(archive), Rule(SEARCH, f"the standard folder {base}")
            )
    bases = ", ".join(str(b) for b in platform_glob_bases()) or "(none)"
    return Found(
        INTERFACE,
        detail=(
            "no Modelica/Library/python_interface beside the executable or under "
            f"the standard folders ({bases})"
        ),
    )


def _cache_root() -> Path:
    """User cache directory for extracted wheels."""
    if sys.platform.startswith("win"):
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / "dstf" / "dymola-interface"


def _cache_signature(archive: Path) -> str:
    """Signature stored in the cache marker so we can invalidate stale or
    corrupt extractions. Pairs source archive size + mtime — cheap to compute,
    catches both wheel updates (Dymola refresh) and partial/interrupted
    extractions that left bogus content (a 32-byte stub `dymola_interface.py`
    actually shipped in our cache once; the touch-only marker happily reused
    it). If the wheel is replaced byte-for-byte the mtime still changes, so
    this is conservative-correct without needing a hash.
    """
    st = archive.stat()
    return f"{archive}|{st.st_size}|{st.st_mtime_ns}"


def _prepare_sys_path(archive: Path) -> Path:
    """Make `import dymola` work. Returns the path added to sys.path."""
    if archive.suffix == ".egg":
        # zipimport supports .egg directly
        entry = str(archive)
    else:
        # Extract .whl to cache once, then add the extract dir.
        extract_dir = _cache_root() / archive.stem
        marker = extract_dir / ".extracted"
        signature = _cache_signature(archive)
        cached_signature = marker.read_text() if marker.exists() else None
        if cached_signature != signature:
            # Wipe and re-extract on first use OR on signature drift (wheel
            # updated, mtime changed, prior extraction corrupted).
            if extract_dir.exists():
                shutil.rmtree(extract_dir)
            extract_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive) as z:
                z.extractall(extract_dir)
            marker.write_text(signature)
        entry = str(extract_dir)
    if entry not in sys.path:
        sys.path.insert(0, entry)
    return Path(entry)


_NOT_FOUND_MESSAGE = """\
Could not find Dymola's Python interface (dymola-*.whl or dymola.egg): {why}

Typical location (Windows):
  C:\\Program Files\\Dymola 2026x\\Modelica\\Library\\python_interface\\dymola-2026.0-py3-none-any.whl

Fix one of:
  1. Set "interface" in the Dymola block of testing.json's "backends"
  2. Set the environment variable DYMOLA_INTERFACE_PATH
"""


_cached_class = None
_cached_path: Path | None = None


def load_dymola_interface(found: Found):
    """Load and return the DymolaInterface class from the archive
    ``DymolaBackend.locate`` found. Cached after first call."""
    global _cached_class, _cached_path
    if _cached_class is not None:
        return _cached_class
    if found.path is None:
        raise RuntimeError(_NOT_FOUND_MESSAGE.format(why=found.detail))
    archive = Path(found.path)
    _cached_path = _prepare_sys_path(archive)
    try:
        from dymola.dymola_interface import (
            DymolaInterface,  # type: ignore[import-not-found]
        )
    except ImportError as e:
        raise RuntimeError(
            f"Found Dymola interface archive at {archive}, but `import dymola` failed: {e}\n"
            f"The archive may be incomplete or incompatible with this Python "
            f"({sys.version.split()[0]})."
        ) from e
    _cached_class = DymolaInterface
    return DymolaInterface


def describe_dymola_interface(found: Found) -> dict:
    """Run the loader in a reporting mode — no raises. Useful for diagnostics."""
    archive = Path(found.path) if found.path else None
    info: dict = {
        "archive": str(archive) if archive else None,
        "format": archive.suffix.lstrip(".") if archive else None,
        "discovered_dirs": [str(p) for p in _find_python_interface_dirs()],
        "sys_path_entry": None,
        "import_ok": False,
        "error": None,
    }
    if archive is None:
        info["error"] = "No archive found"
        return info
    try:
        info["sys_path_entry"] = str(_prepare_sys_path(archive))
        from dymola.dymola_interface import (
            DymolaInterface,  # type: ignore[import-not-found]
        )

        info["import_ok"] = True
        info["dymola_interface_module"] = DymolaInterface.__module__
    except Exception as e:  # pragma: no cover — diagnostic helper
        info["error"] = f"{type(e).__name__}: {e}"
    return info
