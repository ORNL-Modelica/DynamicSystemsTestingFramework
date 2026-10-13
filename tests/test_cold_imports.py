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

The child must probe THIS tree. It runs in another directory, so a relative
`PYTHONPATH` entry (`PYTHONPATH=src`) names nothing there and the child fell
through to whatever `dstf` was installed — the test passed on a tree whose
cycle it never imported. The parent's source root therefore goes first on the
child's `PYTHONPATH`, absolute, and the probe refuses to answer for any other
`dstf` than the parent's.
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
PACKAGE = Path(dstf.__file__).resolve()
SOURCE_ROOT = PACKAGE.parents[1]

PROBE = """
import importlib, pathlib, sys
import dstf
probed = str(pathlib.Path(dstf.__file__).resolve())
assert probed == sys.argv[3], (
    f"the probe imported {probed}, not the tree under test {sys.argv[3]}"
)
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


def _env() -> dict[str, str]:
    """The child's environment: the parent's, with the parent's source root
    first on `PYTHONPATH` — absolute, so it survives the child's `cwd`."""
    inherited = os.environ.get("PYTHONPATH")
    path = [str(SOURCE_ROOT), *([inherited] if inherited else [])]
    return dict(
        os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=os.pathsep.join(path)
    )


def _probe(
    name: str, missing: str, cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", PROBE, name, missing, str(PACKAGE)],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
        env=env,
        timeout=120,
    )


def test_every_module_imports_cold(tmp_path: Path) -> None:
    missing = ",".join(e for e in EXTRAS if importlib.util.find_spec(e) is None)
    env = _env()

    def probe(name: str) -> tuple[str, subprocess.CompletedProcess[str]]:
        return name, _probe(name, missing, tmp_path, env)

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


def test_the_source_root_leads_the_childs_path(monkeypatch) -> None:
    """Whatever `PYTHONPATH` the run was given — a relative one names
    nothing from the child's directory — the tree under test comes first."""
    for inherited in ("src", None):
        if inherited is None:
            monkeypatch.delenv("PYTHONPATH", raising=False)
        else:
            monkeypatch.setenv("PYTHONPATH", inherited)
        first, *rest = _env()["PYTHONPATH"].split(os.pathsep)
        assert first == str(SOURCE_ROOT) and Path(first).is_absolute()
        assert rest == ([inherited] if inherited else [])


def test_a_probe_of_another_tree_fails_naming_both(tmp_path: Path) -> None:
    """A child that imports some other `dstf` is a failure, not a pass on
    the wrong tree: the probe says which it imported and which it was for."""
    other = tmp_path / "other" / "dstf"
    other.mkdir(parents=True)
    (other / "__init__.py").write_text("", encoding="utf-8")
    env = dict(_env(), PYTHONPATH=str(other.parent))

    done = _probe("dstf", "", tmp_path, env)

    assert done.returncode not in (0, 3), done.stdout
    assert str(other / "__init__.py") in done.stderr
    assert str(PACKAGE) in done.stderr
