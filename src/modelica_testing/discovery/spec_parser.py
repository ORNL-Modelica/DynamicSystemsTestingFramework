"""Parse external test specification files (test_spec.json)."""

import json
import logging
from pathlib import Path
from typing import Optional

from ..config import (
    Config,
    DEFAULT_METHOD,
    DEFAULT_STOP_TIME,
    DEFAULT_TOLERANCE,
)
from .test_registry import TestModel

logger = logging.getLogger(__name__)


def parse_test_spec(spec_path: Path) -> list[TestModel]:
    """Parse a test_spec.json file into TestModel entries.

    Format:
    {
      "tests": [
        {
          "model": "MyLib.Examples.PipeTest",
          "variables": ["pipe.T[1]", "medium.T*"],
          "simulation": {
            "stop_time": 100,
            "tolerance": 1e-4,
            "method": "Dassl",
            "number_of_intervals": 500,
            "output_interval": null,
            "timeout": 120
          },
          "comparison": {
            "tolerance": 0.05,
            "variable_overrides": {
              "pipe.T[1]": {"tolerance": 0.1}
            }
          }
        }
      ]
    }

    Minimal entry (all defaults):
    {"model": "MyLib.Examples.Simple", "variables": ["x"]}

    Variable patterns:
    - Explicit: "pipe.T[1]" — exact variable name
    - Glob: "medium.T*" — matched against .mat variable names after simulation
    - Empty list [] — simulate only, no variable comparison
    - Wildcard ["*"] — track all variables
    """
    try:
        data = json.loads(spec_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to read test spec %s: %s", spec_path, e)
        return []

    tests_data = data.get("tests", [])
    if not isinstance(tests_data, list):
        logger.error("'tests' must be a list in %s", spec_path)
        return []

    tests = []
    for entry in tests_data:
        model_id = entry.get("model")
        if not model_id:
            logger.warning("Skipping spec entry with no 'model' field")
            continue

        variables = entry.get("variables", [])
        if not isinstance(variables, list):
            logger.warning("'variables' must be a list for %s", model_id)
            variables = []

        parts = model_id.rsplit(".", 1)
        package_path = parts[0] if len(parts) > 1 else ""
        short_name = parts[-1]

        test = TestModel(
            model_id=model_id,
            mo_file=Path(""),  # Not from a .mo file scan
            package_path=package_path,
            short_name=short_name,
            n_vars=0,  # Will be resolved after simulation for pattern-based
            variable_patterns=variables,
            source="spec",
        )

        # Simulation settings
        sim = entry.get("simulation", {})
        if "stop_time" in sim:
            test.stop_time = float(sim["stop_time"])
        if "tolerance" in sim:
            test.tolerance = float(sim["tolerance"])
        if "method" in sim:
            test.method = str(sim["method"])
        if "number_of_intervals" in sim:
            test.number_of_intervals = int(sim["number_of_intervals"])
        if "output_interval" in sim:
            test.output_interval = float(sim["output_interval"])
        if "timeout" in sim:
            test.timeout = int(sim["timeout"])

        # Comparison settings
        comp = entry.get("comparison", {})
        if "tolerance" in comp:
            test.comparison_tolerance = float(comp["tolerance"])
        if "variable_overrides" in comp:
            test.variable_overrides = comp["variable_overrides"]

        tests.append(test)

    return tests


def add_to_test_spec(
    spec_path: Path,
    model_id: str,
    variables: list[str],
    overwrite: bool = False,
) -> bool:
    """Add or update a test entry in test_spec.json.

    Returns True if the entry was added/updated, False if it already existed
    and overwrite was not set.
    """
    # Load existing or create new
    data = {"tests": []}
    if spec_path.exists():
        try:
            data = json.loads(spec_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
        if "tests" not in data:
            data["tests"] = []

    # Check if model already exists
    existing_idx = None
    for i, entry in enumerate(data["tests"]):
        if entry.get("model") == model_id:
            existing_idx = i
            break

    new_entry = {"model": model_id, "variables": variables}

    if existing_idx is not None:
        if not overwrite:
            return False
        data["tests"][existing_idx] = new_entry
    else:
        data["tests"].append(new_entry)

    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True


def update_test_variables(
    spec_path: Path,
    model_id: str,
    additional_patterns: list[str],
) -> None:
    """Add variable patterns to an existing test entry, or create a new one."""
    data = {"tests": []}
    if spec_path.exists():
        try:
            data = json.loads(spec_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
        if "tests" not in data:
            data["tests"] = []

    # Find existing entry
    found = False
    for entry in data["tests"]:
        if entry.get("model") == model_id:
            existing = set(entry.get("variables", []))
            existing.update(additional_patterns)
            entry["variables"] = sorted(existing)
            found = True
            break

    if not found:
        data["tests"].append({
            "model": model_id,
            "variables": sorted(additional_patterns),
        })

    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def update_test_comparison(
    spec_path: Path,
    update_data: dict,
) -> None:
    """Update or add a test entry's comparison settings in test_spec.json.

    Preserves existing simulation settings and variables. Only merges
    the comparison section from update_data.

    update_data format: {"model": "...", "comparison": {"tolerance": 0.05, ...}}
    """
    data = {"tests": []}
    if spec_path.exists():
        try:
            data = json.loads(spec_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
        if "tests" not in data:
            data["tests"] = []

    model_id = update_data.get("model")
    if not model_id:
        return

    comparison = update_data.get("comparison", {})

    # Find existing entry and merge comparison, preserve everything else
    found = False
    for entry in data["tests"]:
        if entry.get("model") == model_id:
            entry["comparison"] = comparison
            found = True
            break

    if not found:
        data["tests"].append({
            "model": model_id,
            "comparison": comparison,
        })

    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
