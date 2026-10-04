"""Every module under `dstf` imports on its own, in a fresh interpreter.

The suite imports everything in one process, so a module that imports only
because an earlier one already ran never shows there: a module-level import
that closes a cycle (reference_store -> comparison -> comparator ->
reference_store, half-initialised) raised ImportError on a cold
`import dstf.storage.reference_store` while every test that ran after another
stayed green. One subprocess per module, a few at a time.

A module that needs an optional extra not installed here (`nicegui`, `fmpy`,
`OMPython`, `dymola`) is skipped when — and only when — its import fails for
that package; the skipped ones are printed.
"""

from __future__ import annotations

import importlib.util
import os
import pkgutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import dstf

EXTRAS = ("nicegui", "fmpy", "OMPython", "dymola")

PROBE = """
import importlib, sys
missing = set(sys.argv[2].split(",")) - {""}
try:
    importlib.import_module(sys.argv[1])
except ModuleNotFoundError as error:
    if error.name and error.name.split(".")[0] in missing:
        print(error.name)
        sys.exit(3)
    raise
"""


def _modules() -> list[str]:
    return [m.name for m in pkgutil.walk_packages(dstf.__path__, prefix="dstf.")]


def test_every_module_imports_cold(tmp_path: Path) -> None:
    missing = ",".join(e for e in EXTRAS if importlib.util.find_spec(e) is None)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")

    def probe(name: str) -> tuple[str, subprocess.CompletedProcess[str]]:
        done = subprocess.run(
            [sys.executable, "-c", PROBE, name, missing],
            capture_output=True,
            text=True,
            check=False,
            cwd=tmp_path,
            env=env,
        )
        return name, done

    modules = _modules()
    assert len(modules) > 50, modules  # the walk found the package
    with ThreadPoolExecutor(max_workers=2 * (os.cpu_count() or 2)) as pool:
        results = list(pool.map(probe, modules))

    skipped = [(n, d.stdout.strip()) for n, d in results if d.returncode == 3]
    failed = [(n, d.stderr) for n, d in results if d.returncode not in (0, 3)]
    if skipped:
        print(f"skipped (extra not installed: {missing}):")
        for name, extra in skipped:
            print(f"  {name}  needs {extra}")
    assert not failed, "\n\n".join(f"{name}:\n{err}" for name, err in failed)
