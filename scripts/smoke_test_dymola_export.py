"""Windows smoke test for Dymola's translateModelFMU (D65).

Purpose: validate three things that cannot be exercised on Linux WSL and
that the mocked test suite (test_dymola_export_fmu.py) cannot prove:
  1. The ``translateModelFMU`` call signature we pass actually matches
     this Dymola version's API (kwargs: storeResult, modelName,
     fmiVersion, fmiType, includeSource, includeImage).
  2. The Dymola license includes the FMI export option.
  3. ``dymola.cd(str(output_dir))`` actually redirects the FMU output
     on Windows (path escaping / backslash handling).

Run on Windows with Dymola installed. Prints pass/fail; exits 0 on
success, non-zero on any failure.

Usage:
    python scripts/smoke_test_dymola_export.py \\
        --library D:/Modelica/TRANSFORM-UnitTests \\
        --model TRANSFORM.Examples.SomeSimpleModel \\
        [--out C:/tmp/fmu_smoke]

If --library and --model are omitted, the script tries Modelica Standard
Library's ``Modelica.Blocks.Examples.PID_Controller`` which ships with
every Dymola install.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path


def _load_dymola_interface():
    """Import DymolaInterface from the paths Dymola usually installs to.

    Dymola 2026x+ ships a pip-installable wheel (preferred — pip-install the
    ``dymola-*.whl`` from ``...\\python_interface\\`` into your active venv,
    then ``from dymola.dymola_interface import DymolaInterface`` just works).
    Older Dymola versions shipped an egg on ``sys.path``; this fallback tries
    common install dirs before giving up.
    """
    try:
        from dymola.dymola_interface import DymolaInterface
        return DymolaInterface
    except ImportError:
        pass

    candidate_dirs = [
        r"C:\Program Files\Dymola 2026x\Modelica\Library\python_interface\dymola.egg",
        r"C:\Program Files\Dymola 2025x\Modelica\Library\python_interface\dymola.egg",
        r"C:\Program Files\Dymola 2024x\Modelica\Library\python_interface\dymola.egg",
        r"C:\Program Files\Dymola 2023x\Modelica\Library\python_interface\dymola.egg",
    ]
    for d in candidate_dirs:
        if Path(d).exists():
            sys.path.insert(0, d)
            try:
                from dymola.dymola_interface import DymolaInterface  # type: ignore
                return DymolaInterface
            except ImportError:
                continue

    raise RuntimeError(
        "Could not import dymola.dymola_interface. Preferred install "
        "(Dymola 2026x+): pip-install the wheel from "
        r"'C:\Program Files\Dymola 20XXx\Modelica\Library\python_interface\dymola-*.whl' "
        "into your active venv. Older Dymola: add the .egg to PYTHONPATH."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--library",
        help="Absolute path to a Modelica library package.mo (or directory "
             "containing one). Default: Modelica Standard Library (always loaded).",
    )
    parser.add_argument(
        "--model",
        default="Modelica.Blocks.Examples.PID_Controller",
        help="Fully qualified model ID to export. Default: %(default)s",
    )
    parser.add_argument(
        "--out",
        help="Output directory for the .fmu. Default: a fresh system temp dir.",
    )
    args = parser.parse_args()

    out_dir = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="fmu_smoke_"))
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[info] Output directory: {out_dir}")

    print("[step 1] Importing DymolaInterface...")
    try:
        DymolaInterface = _load_dymola_interface()
    except RuntimeError as exc:
        print(f"[FAIL] {exc}")
        return 2

    print("[step 2] Starting Dymola (show_ide=False)...")
    dymola = None
    try:
        dymola = DymolaInterface(showwindow=False)

        if args.library:
            lib_path = Path(args.library)
            if lib_path.is_dir():
                lib_pkg = lib_path / "package.mo"
                if not lib_pkg.exists():
                    print(f"[FAIL] No package.mo in {lib_path}")
                    return 3
                lib_path = lib_pkg
            print(f"[step 3] openModel({lib_path})...")
            if not dymola.openModel(str(lib_path)):
                print(f"[FAIL] openModel returned False. Dymola log: {dymola.getLastErrorLog()}")
                return 4
        else:
            print("[step 3] skipped (no --library; relying on MSL auto-load)")

        print(f"[step 4] cd({out_dir})...")
        if not dymola.cd(str(out_dir)):
            print(f"[FAIL] cd returned False. Dymola log: {dymola.getLastErrorLog()}")
            return 5

        print(f"[step 5] translateModelFMU({args.model}, "
              f"storeResult=False, modelName='', fmiVersion='2', fmiType='all', "
              f"includeSource=False, includeImage=0)...")
        try:
            result = dymola.translateModelFMU(
                args.model,
                storeResult=False,
                modelName="",
                fmiVersion="2",
                fmiType="all",
                includeSource=False,
                includeImage=0,
            )
        except TypeError as exc:
            print(f"[FAIL] translateModelFMU signature mismatch: {exc}")
            print("       This is the kwarg-compatibility risk D65 called out. "
                  "Update DymolaWorker.export_fmu's call site to match this "
                  "Dymola version's accepted kwargs.")
            return 6
        except Exception as exc:
            print(f"[FAIL] translateModelFMU threw: {exc}")
            print(f"       Dymola log: {dymola.getLastErrorLog()}")
            return 7

        print(f"[step 6] translateModelFMU returned: {result!r}")
        if not result:
            print("[FAIL] translateModelFMU returned empty string — typically means "
                  "license is missing the FMI export option, OR the model failed to "
                  "translate. Check the Dymola log for details.")
            print(f"       Dymola log: {dymola.getLastErrorLog()}")
            return 8

        fmu_path = out_dir / f"{result}.fmu"
        print(f"[step 7] Checking {fmu_path}...")
        if fmu_path.exists():
            size_kb = fmu_path.stat().st_size / 1024
            print(f"[PASS] FMU produced at {fmu_path} ({size_kb:.1f} KB)")
            return 0

        candidates = list(out_dir.glob("*.fmu"))
        if candidates:
            print(f"[PASS-with-note] Expected {fmu_path} but found: {candidates}. "
                  f"The glob-fallback in DymolaWorker.export_fmu handles this. "
                  f"Dymola's basename convention may differ from test assumptions.")
            return 0

        print(f"[FAIL] translateModelFMU claimed success but no .fmu in {out_dir}. "
              f"returned={result!r}")
        return 9

    finally:
        if dymola is not None:
            try:
                dymola.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
