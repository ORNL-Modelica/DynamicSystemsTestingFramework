"""Convert between old (abbreviated filename) and new (manifest + numeric ID) reference formats."""

import json
import logging
from pathlib import Path
from typing import Optional

from ..config import Config
from .reference_store import TestManifest

logger = logging.getLogger(__name__)


def convert_to_manifest(
    ref_dir: Path,
    manifest_path: Path,
    index_path: Optional[Path] = None,
) -> int:
    """Convert old-format references to manifest + ref_NNNN.json.

    Reads an index.json (or scans for .json files) in ref_dir, assigns
    stable numeric IDs, renames files to ref_NNNN.json, and writes
    test_manifest.json.

    Returns the number of files converted.
    """
    # Load old index or scan directory
    old_entries = _load_old_index(ref_dir, index_path)
    if not old_entries:
        print("No references found to convert.")
        return 0

    manifest = TestManifest(manifest_path)
    converted = 0

    for model_id, old_filename in sorted(old_entries.items()):
        old_path = ref_dir / old_filename
        if not old_path.exists():
            logger.warning("Referenced file missing: %s", old_path)
            continue

        # Register in manifest
        test_id = manifest.register(model_id)
        new_filename = TestManifest.ref_filename(test_id)
        new_path = ref_dir / new_filename

        if old_path == new_path:
            continue

        # Read, inject test_id, write to new name
        try:
            data = json.loads(old_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to read %s: %s", old_path, e)
            continue

        data["test_id"] = test_id
        new_path.write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8"
        )

        # Remove old file
        old_path.unlink()
        converted += 1
        print(f"  {old_filename} -> {new_filename}  ({model_id})")

    # Remove old index.json if it exists
    idx_path = index_path or (ref_dir / "index.json")
    if idx_path.exists():
        idx_path.unlink()
        print(f"  Removed {idx_path.name}")

    return converted


def convert_from_manifest(
    ref_dir: Path,
    manifest_path: Path,
    library_name: str = "",
) -> int:
    """Convert manifest + ref_NNNN.json back to human-readable filenames.

    Generates filenames from model_id (dots -> underscores, strip library prefix).
    Writes an index.json for the old format.

    Returns the number of files converted.
    """
    manifest = TestManifest(manifest_path)
    if not manifest.exists():
        print("No manifest found.")
        return 0

    active = manifest.active_tests()
    if not active:
        print("No active tests in manifest.")
        return 0

    index = {}
    converted = 0

    for test_id, model_id in sorted(active.items()):
        old_filename = TestManifest.ref_filename(test_id)
        old_path = ref_dir / old_filename
        if not old_path.exists():
            continue

        new_filename = _readable_filename(model_id, library_name)
        new_path = ref_dir / new_filename

        if old_path == new_path:
            continue

        # Read, remove test_id field, write to new name
        try:
            data = json.loads(old_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to read %s: %s", old_path, e)
            continue

        data.pop("test_id", None)
        new_path.write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8"
        )

        old_path.unlink()
        index[model_id] = {
            "filename": new_filename,
            "n_vars": data.get("n_vars", 0),
            "last_updated": data.get("last_updated", ""),
        }
        converted += 1
        print(f"  {old_filename} -> {new_filename}")

    # Write index.json
    if index:
        index_path = ref_dir / "index.json"
        index_path.write_text(
            json.dumps(index, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"  Wrote {index_path.name} with {len(index)} entries")

    return converted


def _load_old_index(
    ref_dir: Path,
    index_path: Optional[Path] = None,
) -> dict[str, str]:
    """Load model_id -> filename mapping from old index.json or by scanning."""
    idx_path = index_path or (ref_dir / "index.json")

    if idx_path.exists():
        try:
            index = json.loads(idx_path.read_text(encoding="utf-8"))
            return {
                model_id: entry["filename"]
                for model_id, entry in index.items()
                if isinstance(entry, dict) and "filename" in entry
            }
        except (json.JSONDecodeError, OSError, KeyError):
            pass

    # Fallback: scan for .json files that contain model_id
    entries = {}
    for json_file in sorted(ref_dir.glob("*.json")):
        if json_file.name in ("index.json", "test_manifest.json"):
            continue
        if json_file.name.startswith("ref_"):
            continue
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            model_id = data.get("model_id")
            if model_id:
                entries[model_id] = json_file.name
        except (json.JSONDecodeError, OSError):
            continue

    return entries


def _readable_filename(model_id: str, library_name: str = "") -> str:
    """Generate a human-readable filename from a model ID."""
    name = model_id
    if library_name and name.startswith(library_name + "."):
        name = name[len(library_name) + 1:]
    name = name.replace("Examples.", "")
    name = name.replace(".", "_")
    return name + ".json"
