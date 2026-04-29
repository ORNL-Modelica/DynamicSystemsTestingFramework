"""Locate and load Dymola's Python interface (DymolaInterface).

Dymola ships its Python integration under
  <Dymola>/Modelica/Library/python_interface/
as either `dymola.egg` (older) or `dymola-<version>-py3-none-any.whl` (newer).

Both are zip archives. `.egg` is directly importable via Python's zipimport by
appending to `sys.path`. `.whl` is not — Python only imports `.whl` content
after `pip install` extracts it. To avoid a per-Dymola-version install step,
we extract the wheel once to a user cache dir and add that dir to `sys.path`.

Discovery order:
  1. Explicit path (CLI flag / config `dymola_interface_path`)
  2. `DYMOLA_INTERFACE_PATH` env var
  3. Standard locations per platform (globs all Dymola versions)

The explicit path may be either the archive itself or the directory containing
it (we look inside).
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Optional


def _dymola_sort_key(path: Path) -> tuple:
    """Sort Dymola install paths so newer versions come first.

    Extracts the year/refresh from names like 'Dymola 2026x' or
    'Dymola 2024x Refresh 1' and sorts descending.
    """
    name = str(path)
    m = re.search(r"Dymola[\s_]?(\d{4})x(?:[\s_]+Refresh[\s_]*(\d+))?", name, re.IGNORECASE)
    if not m:
        return (0, 0, name)
    year = int(m.group(1))
    refresh = int(m.group(2)) if m.group(2) else 0
    # Negate for descending sort
    return (-year, -refresh, name)


def _platform_glob_bases() -> list[Path]:
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


def _find_python_interface_dirs() -> list[Path]:
    """Return all known python_interface directories, newest-first."""
    found: list[Path] = []
    for base in _platform_glob_bases():
        for p in base.glob("Dymola*/Modelica/Library/python_interface"):
            if p.is_dir():
                found.append(p)
        # Linux convention
        for p in base.glob("dymola*/Modelica/Library/python_interface"):
            if p.is_dir():
                found.append(p)
    found.sort(key=_dymola_sort_key)
    return found


def _archive_in_dir(d: Path) -> Optional[Path]:
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


def find_dymola_interface_archive(
    override: Optional[Path] = None,
) -> Optional[Path]:
    """Find the Dymola Python interface archive. Returns the Path or None.

    Accepts either a direct archive path or a directory containing one.
    """
    explicit_candidates: list[Path] = []
    if override:
        explicit_candidates.append(Path(override))
    env = os.environ.get("DYMOLA_INTERFACE_PATH")
    if env:
        explicit_candidates.append(Path(env))

    for cand in explicit_candidates:
        if cand.is_file() and cand.suffix in (".whl", ".egg"):
            return cand
        if cand.is_dir():
            a = _archive_in_dir(cand)
            if a:
                return a

    # Auto-discovery
    for d in _find_python_interface_dirs():
        a = _archive_in_dir(d)
        if a:
            return a
    return None


def _cache_root() -> Path:
    """User cache directory for extracted wheels."""
    if sys.platform.startswith("win"):
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / "modelica-testing" / "dymola-interface"


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
Could not find Dymola's Python interface (dymola.egg or dymola-*.whl).

Searched:
  - Explicit --dymola-interface / dymola_interface_path
  - DYMOLA_INTERFACE_PATH env var
  - Auto-discovery under platform install roots:
    {bases}

Typical location (Windows):
  C:\\Program Files\\Dymola 2026x\\Modelica\\Library\\python_interface\\dymola-2026.0-py3-none-any.whl

Fix one of:
  1. Pass --dymola-interface <path-to-archive-or-dir>
  2. Set environment variable DYMOLA_INTERFACE_PATH
  3. Add "dymola_interface_path" to testing.json
"""


_cached_class = None
_cached_path: Optional[Path] = None


def load_dymola_interface(override_path: Optional[Path] = None):
    """Load and return the DymolaInterface class. Cached after first call."""
    global _cached_class, _cached_path
    if _cached_class is not None:
        return _cached_class
    archive = find_dymola_interface_archive(override_path)
    if archive is None:
        bases = "\n    ".join(str(b) for b in _platform_glob_bases())
        raise RuntimeError(_NOT_FOUND_MESSAGE.format(bases=bases or "(none)"))
    _cached_path = _prepare_sys_path(archive)
    try:
        from dymola.dymola_interface import DymolaInterface  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(
            f"Found Dymola interface archive at {archive}, but `import dymola` failed: {e}\n"
            f"The archive may be incomplete or incompatible with this Python "
            f"({sys.version.split()[0]})."
        ) from e
    _cached_class = DymolaInterface
    return DymolaInterface


def describe_dymola_interface(override_path: Optional[Path] = None) -> dict:
    """Run the loader in a reporting mode — no raises. Useful for diagnostics."""
    archive = find_dymola_interface_archive(override_path)
    info: dict = {
        "archive": str(archive) if archive else None,
        "format": archive.suffix.lstrip(".") if archive else None,
        "search_roots": [str(b) for b in _platform_glob_bases()],
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
        from dymola.dymola_interface import DymolaInterface  # type: ignore[import-not-found]
        info["import_ok"] = True
        info["dymola_interface_module"] = DymolaInterface.__module__
    except Exception as e:  # pragma: no cover — diagnostic helper
        info["error"] = f"{type(e).__name__}: {e}"
    return info
