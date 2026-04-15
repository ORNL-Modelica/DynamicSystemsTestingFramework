#!/usr/bin/env python3
"""Download prebuilt Reference-FMUs release ZIP and extract the FMUs we use.

The FMI org ships prebuilt FMUs as release ZIPs at:
    https://github.com/modelica/Reference-FMUs/releases

Source code lives in the Reference-FMUs repo, but building requires CMake + a
C compiler. Since we only need the binaries, we fetch them directly — this
avoids a build toolchain dependency on every dev and CI machine.

Default behavior: fetch the pinned version into
``examples/fmu/reference-fmus-binaries/``, skip if a matching version is
already present. Extracts only the ``2.0/`` and ``3.0/`` FMUs (skips the
``1.0/`` FMUs since FMPy's support is best for 2.0+ and we don't ship tests
against 1.0). Skips the ``fmusim-*`` standalone-simulator binaries — they're
unrelated to what we do and add ~13MB.

Usage:
    uv run python scripts/fetch_reference_fmus.py
    uv run python scripts/fetch_reference_fmus.py --version v0.0.39
    uv run python scripts/fetch_reference_fmus.py --force        # re-download
"""

from __future__ import annotations

import argparse
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

# Pinned release — bump this intentionally when we want to update.
# See: https://github.com/modelica/Reference-FMUs/releases
DEFAULT_VERSION = "v0.0.39"

# FMI versions we extract. Order matters only for reporting; FMPy handles all.
FMI_VERSIONS_TO_EXTRACT = ("2.0", "3.0")

# Subdirectory prefixes inside the ZIP to skip (noise: standalone simulator
# binaries that aren't our dependency).
SKIP_PREFIXES = ("fmusim-",)

REPO = "modelica/Reference-FMUs"
PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "examples" / "fmu" / "reference-fmus-binaries"
VERSION_MARKER = ".reference-fmus-version"


def _release_zip_url(version: str) -> str:
    """Release asset naming: `Reference-FMUs-<N.N.N>.zip` (no leading 'v')."""
    ver_stripped = version.lstrip("v")
    return (
        f"https://github.com/{REPO}/releases/download/"
        f"{version}/Reference-FMUs-{ver_stripped}.zip"
    )


def _already_fetched(output_dir: Path, version: str) -> bool:
    marker = output_dir / VERSION_MARKER
    return marker.exists() and marker.read_text(encoding="utf-8").strip() == version


def _download(url: str) -> bytes:
    print(f"Downloading {url}")
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            return resp.read()
    except Exception as exc:
        print(f"ERROR: download failed: {exc}", file=sys.stderr)
        raise SystemExit(1)


def _extract(zip_bytes: bytes, output_dir: Path) -> list[str]:
    """Extract FMI 2.0/3.0 FMUs and the LICENSE/README. Return extracted names."""
    extracted: list[str] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            name = info.filename
            # Skip directory entries
            if name.endswith("/"):
                continue
            # Skip fmusim binaries
            if any(name.startswith(p) for p in SKIP_PREFIXES):
                continue
            # Include top-level docs
            if name in ("README.md", "LICENSE.txt"):
                _write_member(zf, info, output_dir)
                extracted.append(name)
                continue
            # Include only the FMI versions we care about
            parts = name.split("/", 1)
            if parts[0] in FMI_VERSIONS_TO_EXTRACT and name.endswith(".fmu"):
                _write_member(zf, info, output_dir)
                extracted.append(name)
    return extracted


def _write_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, output_dir: Path) -> None:
    target = output_dir / info.filename
    target.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(info) as src, open(target, "wb") as dst:
        dst.write(src.read())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        default=DEFAULT_VERSION,
        help=f"Reference-FMUs release tag to fetch (default: {DEFAULT_VERSION})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output directory (default: {DEFAULT_OUTPUT.relative_to(PROJECT_ROOT)})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if the pinned version is already present.",
    )
    args = parser.parse_args(argv)

    if not args.force and _already_fetched(args.output, args.version):
        print(f"Reference-FMUs {args.version} already present at {args.output}")
        print("(use --force to re-download)")
        return 0

    url = _release_zip_url(args.version)
    zip_bytes = _download(url)
    args.output.mkdir(parents=True, exist_ok=True)
    extracted = _extract(zip_bytes, args.output)

    # Record the version we just fetched.
    (args.output / VERSION_MARKER).write_text(args.version + "\n", encoding="utf-8")

    fmus = [n for n in extracted if n.endswith(".fmu")]
    print(f"\nExtracted {len(fmus)} FMUs from Reference-FMUs {args.version}:")
    for name in sorted(fmus):
        print(f"  {name}")
    print(f"\nOutput: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
