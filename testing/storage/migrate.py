"""Migrate old buildingspy reference results (.txt) to the new JSON format."""

import logging
import re
from pathlib import Path
from typing import Optional

from ..config import Config
from ..discovery.test_registry import TestModel, generate_reference_filename

logger = logging.getLogger(__name__)


def _parse_buildingspy_txt(path: Path) -> Optional[dict]:
    """Parse a buildingspy-format .txt reference file.

    Format:
        last-generated=2021-05-05
        statistics-initialization=
        { "numerical Jacobians": "0", ... }
        statistics-simulation=
        { "numerical Jacobians": "0", ... }
        unitTests.x[1]=[1., 1., ...]
        time=[0., 1000.]
        unitTests.x[2]=[...]
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.error("Cannot read %s: %s", path, e)
        return None

    result = {
        "last_generated": None,
        "statistics_initialization": None,
        "statistics_simulation": None,
        "time": None,
        "variables": {},
    }

    # Extract last-generated
    m = re.search(r'last-generated=(.+)', text)
    if m:
        result["last_generated"] = m.group(1).strip()

    # Extract statistics blocks
    for stat_name in ("initialization", "simulation"):
        pattern = rf'statistics-{stat_name}=\s*\n(\{{[^}}]*\}})'
        m = re.search(pattern, text, re.DOTALL)
        if m:
            result[f"statistics_{stat_name}"] = m.group(1).strip()

    # Extract time array
    m = re.search(r'time=\[([^\]]+)\]', text)
    if m:
        result["time"] = _parse_float_array(m.group(1))

    # Extract unitTests.x[N] arrays
    for m in re.finditer(r'unitTests\.x\[(\d+)\]=\[([^\]]+)\]', text):
        idx = int(m.group(1))
        values = _parse_float_array(m.group(2))
        result["variables"][idx] = values

    if not result["variables"]:
        return None

    return result


def _parse_float_array(text: str) -> list[float]:
    """Parse a comma-separated float array from buildingspy format."""
    parts = text.split(",")
    values = []
    for p in parts:
        p = p.strip().rstrip(".")
        if p:
            try:
                values.append(float(p))
            except ValueError:
                values.append(0.0)
    return values


def _build_filename_to_model_map(
    test_lookup: Optional[dict[str, "TestModel"]],
) -> dict[str, str]:
    """Build a map from buildingspy-style filenames to model_ids.

    Buildingspy filenames are model_ids with dots replaced by underscores:
      TRANSFORM.Blocks.Examples.EasingRamp_Test -> TRANSFORM_Blocks_Examples_EasingRamp_Test

    Since model names can contain underscores, we can't reverse this without
    knowing the actual model_ids. So we use discovered tests as ground truth.
    """
    if not test_lookup:
        return {}

    result = {}
    for model_id in test_lookup:
        bp_name = model_id.replace(".", "_")
        result[bp_name] = model_id
    return result


def _model_id_from_filename(
    filename: str,
    filename_map: Optional[dict[str, str]] = None,
) -> str:
    """Convert buildingspy filename back to model_id.

    Uses the filename_map (from discovered tests) for accurate conversion.
    Falls back to a heuristic if no map is available.
    """
    stem = Path(filename).stem  # Remove .txt

    # Try exact match from discovery
    if filename_map and stem in filename_map:
        return filename_map[stem]

    # Fallback: replace underscores with dots (lossy for model names with underscores)
    logger.warning(
        "No discovered test match for %s — using fallback conversion", stem
    )
    return stem.replace("_", ".")


def _convert_old_statistics(parsed: dict) -> Optional[dict]:
    """Convert old buildingspy statistics strings to structured dict.

    Old format stored statistics as JSON-like strings:
        statistics-initialization=
        { "numerical Jacobians": "0", "nonlinear": "2, 2" }

    New format uses a cleaner structure:
        {"initialization": {"numerical_jacobians": 0, "nonlinear": "2, 2"}, ...}
    """
    import json as json_mod

    stats = {}

    for phase in ("initialization", "simulation"):
        raw = parsed.get(f"statistics_{phase}")
        if raw is None:
            continue

        try:
            old = json_mod.loads(raw)
        except (json_mod.JSONDecodeError, TypeError):
            continue

        section = {}
        for key, value in old.items():
            # Normalize key: "numerical Jacobians" -> "numerical_jacobians"
            new_key = key.strip().lower().replace(" ", "_")

            # Normalize value
            value = value.strip() if isinstance(value, str) else str(value)
            if not value:
                continue

            # Single integer values
            if value.isdigit():
                section[new_key] = int(value)
            else:
                # Comma-separated lists (e.g., "2, 2" for nonlinear system sizes)
                section[new_key] = value

        if section:
            stats[phase] = section

    return stats if stats else None


def migrate_buildingspy_references(
    source_dir: Path,
    config: Config,
    test_lookup: Optional[dict[str, TestModel]] = None,
) -> int:
    """Migrate all .txt files from a buildingspy ReferenceResults directory.

    Args:
        source_dir: Directory containing buildingspy .txt files
                    (e.g., TRANSFORM-UnitTests/ReferenceResults/Dymola/)
        config: Testing system config (determines output location)
        test_lookup: Optional dict of model_id -> TestModel for enrichment

    Returns: number of files migrated
    """
    from .reference_store import ReferenceStore
    from ..simulation.result_reader import VariableResult, TestResult
    import numpy as np

    if not source_dir.exists():
        logger.error("Source directory does not exist: %s", source_dir)
        return 0

    txt_files = sorted(source_dir.glob("*.txt"))
    if not txt_files:
        logger.warning("No .txt files found in %s", source_dir)
        return 0

    # Build filename -> model_id map from discovered tests
    filename_map = _build_filename_to_model_map(test_lookup)

    store = ReferenceStore(config)
    migrated = 0

    for txt_file in txt_files:
        parsed = _parse_buildingspy_txt(txt_file)
        if parsed is None:
            logger.warning("Skipping %s: no variables found", txt_file.name)
            continue

        model_id = _model_id_from_filename(txt_file.name, filename_map)
        time_array = parsed.get("time")

        if time_array is None or len(time_array) < 2:
            logger.warning("Skipping %s: no time array", txt_file.name)
            continue

        # Build VariableResult objects.
        # All variables must share a single time vector. In buildingspy format,
        # different variables can have different point counts (e.g., constants
        # have 2 points, trajectories have 101). Interpolate all to the longest
        # time grid so no information is lost.
        t_start = time_array[0]
        t_end = time_array[-1]

        max_points = max(len(v) for v in parsed["variables"].values())
        shared_time = np.linspace(t_start, t_end, max_points)

        variables = []
        for idx in sorted(parsed["variables"].keys()):
            values = parsed["variables"][idx]
            n_points = len(values)

            if n_points == max_points:
                vals = np.array(values)
            else:
                # Interpolate to the shared time grid
                var_time = np.linspace(t_start, t_end, n_points)
                vals = np.interp(shared_time, var_time, values)

            variables.append(VariableResult(
                index=idx,
                time=shared_time,
                values=vals,
            ))

        # Convert old statistics to structured format
        statistics = _convert_old_statistics(parsed)

        # Build a synthetic TestResult
        result = TestResult(
            model_id=model_id,
            success=True,
            variables=variables,
            statistics=statistics,
        )

        # Build a minimal TestModel for the store
        test = test_lookup.get(model_id) if test_lookup else None
        if test is None:
            parts = model_id.rsplit(".", 1)
            test = TestModel(
                model_id=model_id,
                mo_file=Path(""),
                package_path=parts[0] if len(parts) > 1 else "",
                short_name=parts[-1],
                n_vars=len(variables),
            )

        if store.store_reference(test, result):
            migrated += 1
            logger.info("Migrated: %s (%d vars)", model_id, len(variables))
        else:
            logger.warning("Failed to store: %s", model_id)

    return migrated
