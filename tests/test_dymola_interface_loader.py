"""Unit tests for the Dymola Python interface loader's wheel-extract cache.

Regression test for the bug where a stale or partial wheel extraction
(seen in the wild as a 32-byte `dymola_interface.py` stub) was reused
indefinitely because the `.extracted` marker file only stored the source
path, not a content signature. The marker now stores `path|size|mtime`
of the source archive; a mismatch wipes and re-extracts.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest


def _write_minimal_wheel(path: Path, payload: bytes) -> None:
    """Write a wheel-shaped zip containing one file: dymola/dymola_interface.py."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("dymola/__init__.py", b"")
        z.writestr("dymola/dymola_interface.py", payload)


@pytest.fixture
def fake_wheel(tmp_path: Path) -> Path:
    archive = tmp_path / "dymola-test-py3-none-any.whl"
    _write_minimal_wheel(archive, b"# generation 1: real content " * 100)
    return archive


def test_extracts_wheel_on_first_call(monkeypatch, tmp_path, fake_wheel):
    from dstf.backends.dymola import interface_loader as il

    cache_root = tmp_path / "cache"
    monkeypatch.setattr(il, "_cache_root", lambda: cache_root)

    entry = il._prepare_sys_path(fake_wheel)

    extracted = entry / "dymola" / "dymola_interface.py"
    assert extracted.exists()
    assert extracted.read_bytes().startswith(b"# generation 1")
    marker = entry / ".extracted"
    assert marker.exists()
    # Signature is path|size|mtime — verify all three present so a stale
    # path-only marker (the pre-fix shape) would mismatch and re-extract.
    parts = marker.read_text().split("|")
    assert len(parts) == 3
    assert parts[0] == str(fake_wheel)


def test_reuses_cache_when_signature_matches(monkeypatch, tmp_path, fake_wheel):
    from dstf.backends.dymola import interface_loader as il

    cache_root = tmp_path / "cache"
    monkeypatch.setattr(il, "_cache_root", lambda: cache_root)

    entry1 = il._prepare_sys_path(fake_wheel)
    extracted = entry1 / "dymola" / "dymola_interface.py"
    mtime_before = extracted.stat().st_mtime_ns

    entry2 = il._prepare_sys_path(fake_wheel)

    assert entry1 == entry2
    assert extracted.stat().st_mtime_ns == mtime_before, (
        "second call should not re-extract when the source archive is unchanged"
    )


def test_reextracts_when_wheel_signature_changes(monkeypatch, tmp_path, fake_wheel):
    """The original bug: a wheel was replaced in place (e.g. Dymola refresh)
    but our cache held a now-stale extraction. The path-only marker never
    invalidated. Now the marker records size+mtime so any wheel change is
    detected and the cache is wiped."""
    from dstf.backends.dymola import interface_loader as il

    cache_root = tmp_path / "cache"
    monkeypatch.setattr(il, "_cache_root", lambda: cache_root)

    il._prepare_sys_path(fake_wheel)

    # Replace the wheel with different content; bump mtime explicitly so this
    # test isn't a flaky race-with-filesystem-resolution.
    _write_minimal_wheel(fake_wheel, b"# generation 2: different content " * 200)
    import os
    import time

    new_mtime = time.time() + 5
    os.utime(fake_wheel, (new_mtime, new_mtime))

    entry = il._prepare_sys_path(fake_wheel)
    extracted = entry / "dymola" / "dymola_interface.py"
    assert extracted.read_bytes().startswith(b"# generation 2"), (
        "cache should re-extract when the source wheel changes"
    )


def test_reextracts_when_cache_marker_uses_old_path_only_format(
    monkeypatch,
    tmp_path,
    fake_wheel,
):
    """A cache populated by a pre-fix DSTF version has a marker containing
    just `str(archive)` — no size or mtime. Treat it as a mismatch and
    re-extract so users upgrading don't keep using broken caches."""
    from dstf.backends.dymola import interface_loader as il

    cache_root = tmp_path / "cache"
    monkeypatch.setattr(il, "_cache_root", lambda: cache_root)

    # Simulate a pre-fix cache: extract dir + path-only marker + a stub file.
    extract_dir = cache_root / fake_wheel.stem
    (extract_dir / "dymola").mkdir(parents=True)
    (extract_dir / "dymola" / "dymola_interface.py").write_bytes(b"# stub")
    (extract_dir / ".extracted").write_text(str(fake_wheel))  # old format

    il._prepare_sys_path(fake_wheel)

    extracted = extract_dir / "dymola" / "dymola_interface.py"
    assert extracted.read_bytes().startswith(b"# generation 1"), (
        "stub from a pre-fix cache should be replaced by a real extraction"
    )


# ---------------------------------------------------------------------------
# One interface per process: the class is its archive's, never another's
# ---------------------------------------------------------------------------


def _interface_wheel(directory: Path, version: str) -> Path:
    """A `python_interface` folder's wheel whose `DymolaInterface` says
    which Dymola it is of."""
    directory.mkdir(parents=True)
    wheel = directory / f"dymola-{version}-py3-none-any.whl"
    _write_minimal_wheel(
        wheel, f"class DymolaInterface:\n    VERSION = {version!r}\n".encode()
    )
    return wheel


@pytest.fixture
def il(monkeypatch, tmp_path):
    """The loader as a fresh process has it: nothing loaded, no `dymola`
    imported, its cache under `tmp_path` — and this process as it was,
    afterwards."""
    import sys

    from dstf.backends.dymola import interface_loader

    monkeypatch.setattr(interface_loader, "_cache_root", lambda: tmp_path / "cache")
    monkeypatch.setattr(interface_loader, "_loaded", None)
    monkeypatch.setattr(sys, "path", list(sys.path))
    held = {name: sys.modules.pop(name) for name in list(sys.modules) if _is_ours(name)}
    try:
        yield interface_loader
    finally:
        for name in [name for name in sys.modules if _is_ours(name)]:
            del sys.modules[name]
        sys.modules.update(held)


def _is_ours(name: str) -> bool:
    return name == "dymola" or name.startswith("dymola.")


def _found(wheel: Path):
    from dstf.backends.dymola.interface_loader import INTERFACE
    from dstf.backends.locate import SETTING, Found, Rule

    return Found(INTERFACE, str(wheel), Rule(SETTING, "interface"))


def test_the_class_is_the_one_of_the_archive_that_was_found(il, tmp_path):
    older = _interface_wheel(tmp_path / "2024", "2024.0")
    cls = il.load_dymola_interface(_found(older))
    assert cls.VERSION == "2024.0"
    # The same archive again is the same class: nothing is imported twice.
    assert il.load_dymola_interface(_found(older)) is cls


def test_a_second_install_s_interface_is_refused_not_served_the_first(il, tmp_path):
    """A second Dymola entry opened in one process (the tool's Change
    backend…) was handed the FIRST entry's class: the cache was the
    process's, not the archive's. Both archives are the Python package
    `dymola`, and a process imports one."""
    older = _interface_wheel(tmp_path / "2024", "2024.0")
    newer = _interface_wheel(tmp_path / "2026", "2026.0")
    assert il.load_dymola_interface(_found(older)).VERSION == "2024.0"
    with pytest.raises(RuntimeError) as exc:
        il.load_dymola_interface(_found(newer))
    assert str(exc.value) == (
        f"This process has already loaded Dymola's Python interface from {older}. "
        f"The one at {newer} cannot be loaded beside it: both are the Python "
        "package `dymola`, and a process holds one. Restart dstf to run with "
        "this Dymola."
    )
    # …and `check` does not call the second one importable either.
    info = il.describe_dymola_interface(_found(newer))
    assert info["import_ok"] is False
    assert "has already loaded" in info["error"]
    assert il.describe_dymola_interface(_found(older))["import_ok"] is True


def test_no_archive_is_not_found_whatever_was_loaded_before(il, tmp_path):
    from dstf.backends.dymola.interface_loader import INTERFACE
    from dstf.backends.locate import Found

    il.load_dymola_interface(_found(_interface_wheel(tmp_path / "2024", "2024.0")))
    with pytest.raises(RuntimeError, match="Could not find Dymola's Python interface"):
        il.load_dymola_interface(Found(INTERFACE, detail="nowhere"))


def test_a_dymola_package_from_elsewhere_is_not_the_archive_s(il, tmp_path):
    """`import dymola` answered by a package that is not the archive's —
    one already imported — is not this install's interface."""
    import sys
    import types

    stray = types.ModuleType("dymola")
    stray.__path__ = [str(tmp_path / "elsewhere")]
    interface = types.ModuleType("dymola.dymola_interface")
    interface.__file__ = str(tmp_path / "elsewhere" / "dymola_interface.py")
    interface.DymolaInterface = type(
        "DymolaInterface", (), {"__module__": "dymola.dymola_interface"}
    )
    sys.modules["dymola"] = stray
    sys.modules["dymola.dymola_interface"] = interface
    wheel = _interface_wheel(tmp_path / "2024", "2024.0")
    with pytest.raises(RuntimeError, match="is not the archive's"):
        il.load_dymola_interface(_found(wheel))


def test_a_session_that_changes_backend_is_refused_the_second_dymola(
    il, tmp_path, monkeypatch
):
    """Through the tool: the project opened on "Dymola 2024", its queue's
    backend built; then reopened on "Dymola 2026" (Change backend…). The
    queue of the second is refused with the sentence — it used to drive
    Dymola 2026 through 2024's interface."""
    pytest.importorskip("nicegui")
    import json

    from dstf.authoring.session import QueueError, Session
    from dstf.backends import select_backend

    lib = tmp_path / "Lib"
    lib.mkdir()
    (lib / "package.mo").write_text("within ;\npackage Lib\nend Lib;\n")
    backends = {}
    for year in ("2024", "2026"):
        install = tmp_path / f"dymola-{year}x"
        program = install / "bin64" / "dymola.sh"
        program.parent.mkdir(parents=True)
        program.write_text("#!/bin/sh\n")
        program.chmod(0o755)
        _interface_wheel(
            install / "Modelica" / "Library" / "python_interface", f"{year}.0"
        )
        backends[f"Dymola {year}"] = {"executables": [str(program)]}
    cfg = tmp_path / "testing.json"
    cfg.write_text(json.dumps({"source_path": "Lib", "backends": backends}))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("DYMOLA_INTERFACE_PATH", raising=False)

    session = Session()
    session.open(cfg, backend="Dymola 2024", work_dir=tmp_path / "work")
    first = select_backend(session.project.config, persistent=True, to_run=True)
    assert il._loaded is not None and il._loaded[2].VERSION == "2024.0"
    assert first.interface_of(first.config).path.endswith(
        "dymola-2024.0-py3-none-any.whl"
    )

    session.open(cfg, backend="Dymola 2026", work_dir=tmp_path / "work")
    assert session.project.config.backend_name == "Dymola 2026"
    with pytest.raises(QueueError, match="has already loaded Dymola's Python"):
        session._open_pool()
    assert "Restart dstf to run with this Dymola." in session.queue_refusal
    # the tool's Queue names only what can be done there: it has no --batch
    assert "--batch" not in session.queue_refusal
