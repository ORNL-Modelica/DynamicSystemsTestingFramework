"""scripts/convert_baseline_names.py over reference trees built in the test.

The script runs as a subprocess, the way a library owner runs it. Every
expectation is read back from the files. The new names are LITERALS (their
codes computed once outside this suite with ``sha256sum``):
``Lib.A`` -> 6e187be76792, ``Lib.B`` -> 452b135a045c, and
``TRANSFORM.Media.IdealGases.Examples.HeXeAlternate`` -> 121677875640, the
real code that is all digits, so a run that took it for an old number would
show here.
"""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "convert_baseline_names.py"

HEXE = "TRANSFORM.Media.IdealGases.Examples.HeXeAlternate"


def _baseline(model_id: str, test_id: str, eol: str = "\n") -> tuple[bytes, bytes]:
    """A baseline file as DSTF wrote it, and the same file without its test_id line."""
    lines = [
        "{",
        f'  "model_id": "{model_id}",',
        f'  "test_id": "{test_id}",',
        '  "status": "active",',
        '  "simulation": {',
        '    "stop_time": 1.0',
        "  },",
        '  "variables": {}',
        "}",
    ]
    old = eol.join(lines) + eol
    new = eol.join(line for line in lines if '"test_id"' not in line) + eol
    return old.encode(), new.encode()


A_LINUX = _baseline("Lib.A", "0001")
B_LINUX = _baseline("Lib.B", "0002", eol="\r\n")
A_WIN = _baseline("Lib.A", "0001")
A_WIN_DUP = _baseline("Lib.A", "0005")
HEXE_OM = _baseline(HEXE, "0003")

SOFT = b'{\n  "time": [0.0, 1.0],\n  "x": [1.0, 2.0]\n}\n'
FROZEN_META = (
    b'{\n  "kind": "frozen",\n  "format": "csv",\n  "data_file": "analytic.csv"\n}\n'
)
FROZEN_CSV = b"time,x\n0,1\n1,2\n"


def _external(path: str) -> bytes:
    return (
        '{\n  "kind": "external",\n  "format": "json",\n'
        f'  "path": "{path}",\n'
        '  "variables": {\n    "x": "x"\n  }\n}\n'
    ).encode()


#: From libA's Dymola/linux folder (what a relative companion path is read
#: against) into libB's OpenModelica/linux folder: another root.
INTO_B = "../../../../libB/ReferenceResults/OpenModelica/linux/"


def _write(root: Path, files: dict[str, bytes]) -> None:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def _tree(base: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(base)): p.read_bytes()
        for p in sorted(base.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    """Two libraries' reference roots; libA's companion points into libB."""
    a = tmp_path / "libA" / "ReferenceResults"
    b = tmp_path / "libB" / "ReferenceResults"
    _write(
        a,
        {
            "Dymola/linux/ref_0001.json": A_LINUX[0],
            "Dymola/linux/ref_0002.json": B_LINUX[0],
            "Dymola/linux/.id_high_water.json": b'{\n  "high_water": 2\n}\n',
            "Dymola/linux/soft_checks/ref_0001/openmodelica.json": SOFT,
            "Dymola/linux/companions/ref_0002/om.json": _external(
                INTO_B + "ref_0003.json"
            ),
            "Dymola/windows/ref_0001.json": A_WIN[0],
            "Dymola/windows/ref_0005.json": A_WIN_DUP[0],
            "Dymola/windows/.id_high_water.json": b'{\n  "high_water": 5\n}\n',
        },
    )
    _write(
        b,
        {
            "OpenModelica/linux/ref_0003.json": HEXE_OM[0],
            "OpenModelica/linux/.id_high_water.json": b'{\n  "high_water": 3\n}\n',
            "OpenModelica/linux/companions/ref_0003/analytic.json": FROZEN_META,
            "OpenModelica/linux/companions/ref_0003/analytic.csv": FROZEN_CSV,
        },
    )
    return a, b


def _run(*args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True,
        text=True,
        timeout=60,
    )


KEEP = ("--keep", "Dymola/windows/ref_0001.json")


def test_help_says_what_it_does():
    out = _run("--help")
    assert out.returncode == 0
    assert "ref_0001.json" in out.stdout
    assert "--dry-run" in out.stdout and "--keep" in out.stdout


def test_converts_every_root_at_once(roots, tmp_path):
    a, b = roots
    out = _run(a, b, *KEEP)
    assert out.returncode == 0, out.stderr

    assert _tree(a) == {
        # Only the test_id line is gone; Lib.B's CRLF line endings are kept.
        "Dymola/linux/ref_6e187be76792.json": A_LINUX[1],
        "Dymola/linux/ref_452b135a045c.json": B_LINUX[1],
        "Dymola/linux/soft_checks/ref_6e187be76792/openmodelica.json": SOFT,
        # The path into the other root now names that file's new name.
        "Dymola/linux/companions/ref_452b135a045c/om.json": _external(
            INTO_B + "ref_121677875640.json"
        ),
        # The duplicate ref_0005 is deleted; --keep's file is converted.
        "Dymola/windows/ref_6e187be76792.json": A_WIN[1],
    }
    assert _tree(b) == {
        "OpenModelica/linux/ref_121677875640.json": HEXE_OM[1],
        "OpenModelica/linux/companions/ref_121677875640/analytic.json": FROZEN_META,
        "OpenModelica/linux/companions/ref_121677875640/analytic.csv": FROZEN_CSV,
    }
    assert b"\r\n" in B_LINUX[1]  # the CRLF case is real


def test_second_run_has_nothing_to_do(roots):
    a, b = roots
    assert _run(a, b, *KEEP).returncode == 0
    before = _tree(a.parent.parent)
    again = _run(a, b)
    assert again.returncode == 0, again.stderr
    assert again.stdout.splitlines() == [
        "0 files renamed, 0 test_id lines deleted, 0 second files deleted, "
        "0 role folders renamed, 0 companion paths rewritten, "
        "0 .id_high_water.json deleted",
        "nothing to do",
    ]
    assert _tree(a.parent.parent) == before


def test_dry_run_writes_nothing(roots):
    a, b = roots
    before = _tree(a.parent.parent)
    out = _run(a, b, *KEEP, "--dry-run")
    assert out.returncode == 0, out.stderr
    dl, dw, om = a / "Dymola/linux", a / "Dymola/windows", b / "OpenModelica/linux"
    assert out.stdout.splitlines() == [
        "4 files renamed, 4 test_id lines deleted, 1 second files deleted, "
        "3 role folders renamed, 1 companion paths rewritten, "
        "3 .id_high_water.json deleted",
        f"  delete {dw}/ref_0005.json (a second file of Lib.A; ref_0001.json is kept)",
        f"  rename {dl}/ref_0001.json -> ref_6e187be76792.json",
        f"  rename {dl}/ref_0002.json -> ref_452b135a045c.json",
        f"  rename {dw}/ref_0001.json -> ref_6e187be76792.json",
        f"  rename {om}/ref_0003.json -> ref_121677875640.json",
        f"  rename {dl}/soft_checks/ref_0001 -> ref_6e187be76792",
        f"  rename {dl}/companions/ref_0002 -> ref_452b135a045c",
        f"  rename {om}/companions/ref_0003 -> ref_121677875640",
        f"  edit   {dl}/companions/ref_0002/om.json: path {INTO_B}ref_0003.json "
        f"-> {INTO_B}ref_121677875640.json",
        f"  delete {dl}/.id_high_water.json",
        f"  delete {dw}/.id_high_water.json",
        f"  delete {om}/.id_high_water.json",
        "dry run: nothing written",
    ]
    assert _tree(a.parent.parent) == before


def test_a_duplicate_no_keep_names_is_refused(roots):
    a, b = roots
    before = _tree(a.parent.parent)
    out = _run(a, b)
    assert out.returncode == 1
    assert out.stderr.splitlines() == [
        "REFUSED -- 1 problem(s); nothing written:",
        f"  {a / 'Dymola/windows'}: Lib.A is held by ref_0001.json, ref_0005.json "
        "-- name the one to keep with --keep",
    ]
    assert _tree(a.parent.parent) == before


def test_a_companion_path_outside_the_roots_is_refused(roots):
    a, _b = roots
    before = _tree(a.parent.parent)
    out = _run(a, *KEEP)  # libB, which the companion points into, not given
    assert out.returncode == 1
    assert out.stderr.splitlines() == [
        "REFUSED -- 1 problem(s); nothing written:",
        f"  {a / 'Dymola/linux/companions/ref_0002/om.json'}: its path "
        f"{INTO_B}ref_0003.json names a baseline outside the roots given -- give that baseline's root "
        "in the same run",
    ]
    assert _tree(a.parent.parent) == before


@pytest.mark.skipif(sys.platform == "win32", reason="a drive path is a path there")
@pytest.mark.parametrize(
    "path",
    [
        "D:\\Lib\\ReferenceResults\\Dymola\\windows\\ref_0011.json",
        "D:/Lib/ReferenceResults/Dymola/windows/ref_0011.json",
    ],
    ids=["backslashes", "slashes"],
)
def test_a_windows_drive_companion_path_is_refused_off_windows(roots, path):
    """Off Windows a drive path can be neither resolved nor rewritten: run
    as it was, the companion was left naming the old file and the run said
    nothing (a ``D:/`` path read as relative, a ``D:\\`` one as no ref
    name). Refused, with what to do, and nothing written."""
    a, b = roots
    meta = a / "Dymola/linux/companions/ref_0002/om.json"
    meta.write_bytes(_external(path.replace("\\", "\\\\")))
    before = _tree(a.parent.parent)
    out = _run(a, b, *KEEP)
    assert out.returncode == 1
    assert out.stderr.splitlines() == [
        "REFUSED -- 1 problem(s); nothing written:",
        f"  {meta}: its path {path} is on a Windows drive -- run the convert "
        "on Windows, or make the path relative to its baseline's folder",
    ]
    assert _tree(a.parent.parent) == before


def test_a_one_line_file_is_refused(roots):
    a, b = roots
    one_line = b'{"model_id": "Lib.C", "test_id": "0004", "status": "active"}'
    (b / "OpenModelica/linux/ref_0004.json").write_bytes(one_line)
    before = _tree(a.parent.parent)
    out = _run(a, b, *KEEP)
    assert out.returncode == 1
    assert out.stderr.splitlines() == [
        "REFUSED -- 1 problem(s); nothing written:",
        f"  {b / 'OpenModelica/linux/ref_0004.json'}: is one line -- its test_id "
        "cannot be deleted as a line; reformat the file with one key per line",
    ]
    assert _tree(a.parent.parent) == before
