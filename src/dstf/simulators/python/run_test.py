"""Framework-shipped Python driver for a single test (D80).

Invoked by PythonRunner as:
    python run_test.py <user_file> <stop_time> <tolerance> <result_path>

The user file must define:
    simulate(stop_time: float, tolerance: float) -> dict

returning a dict with:
    "time":      list[float] (monotonically non-decreasing)
    "variables": dict[str, list[float]]  (each same length as "time")

Result file is JSON:
    {"success": true,  "time": [...], "variables": {...}}
or on failure:
    {"success": false, "error": "...", "time": [], "variables": {}}

Exceptions are caught so the framework always gets a structured result
rather than a process-level crash.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import traceback
from pathlib import Path


def _load_user_module(user_file: Path):
    # Allow sibling imports from the user's file directory.
    parent = str(user_file.parent.resolve())
    if parent not in sys.path:
        sys.path.insert(0, parent)
    spec = importlib.util.spec_from_file_location("_user_test", user_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {user_file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_failure(result_path: Path, message: str) -> None:
    result_path.write_text(
        json.dumps(
            {
                "success": False,
                "error": message,
                "time": [],
                "variables": {},
            }
        ),
        encoding="utf-8",
    )


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(
            "Usage: run_test.py <user_file> <stop_time> <tolerance> <result_path>",
            file=sys.stderr,
        )
        return 2

    user_file = Path(argv[0])
    try:
        stop_time = float(argv[1])
        tolerance = float(argv[2])
    except ValueError as exc:
        print(f"Invalid numeric arg: {exc}", file=sys.stderr)
        return 2
    result_path = Path(argv[3])

    try:
        module = _load_user_module(user_file)
        if not hasattr(module, "simulate"):
            raise AttributeError(
                f"{user_file.name} must define simulate(stop_time, tolerance)"
            )
        payload = module.simulate(stop_time, tolerance)
        if (
            not isinstance(payload, dict)
            or "time" not in payload
            or "variables" not in payload
        ):
            raise ValueError(
                "simulate() must return a dict with 'time' and 'variables' keys"
            )
        if not isinstance(payload["variables"], dict):
            raise ValueError("'variables' must be a dict of name -> list[float]")

        time_list = list(payload["time"])
        variables = {str(k): list(v) for k, v in payload["variables"].items()}
        for name, values in variables.items():
            if len(values) != len(time_list):
                raise ValueError(
                    f"variable '{name}' has {len(values)} samples; "
                    f"expected {len(time_list)} to match 'time'"
                )

        result_path.write_text(
            json.dumps(
                {
                    "success": True,
                    "time": time_list,
                    "variables": variables,
                }
            ),
            encoding="utf-8",
        )
        print(f"OK: {len(variables)} variables, {len(time_list)} time points")
        return 0
    except BaseException:
        tb = traceback.format_exc()
        print(f"FAIL:\n{tb}", file=sys.stderr)
        try:
            _write_failure(result_path, tb)
        except Exception:
            return 2
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
