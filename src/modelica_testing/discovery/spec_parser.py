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
          "stop_time": 100,
          "tolerance": 1e-4,
          "method": "Dassl",
          "number_of_intervals": 500
        }
      ]
    }

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

        # Optional simulation parameter overrides
        if "stop_time" in entry:
            test.stop_time = float(entry["stop_time"])
        if "tolerance" in entry:
            test.tolerance = float(entry["tolerance"])
        if "method" in entry:
            test.method = str(entry["method"])
        if "number_of_intervals" in entry:
            test.number_of_intervals = int(entry["number_of_intervals"])
        if "output_interval" in entry:
            test.output_interval = float(entry["output_interval"])
        if "timeout" in entry:
            test.timeout = int(entry["timeout"])
        if "error_expected" in entry:
            test.error_expected = float(entry["error_expected"])

        tests.append(test)

    return tests
