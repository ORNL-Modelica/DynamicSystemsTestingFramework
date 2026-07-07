"""Parse external test specification files (test_spec.json)."""

import json
import logging
from pathlib import Path

from .test_registry import TestModel

logger = logging.getLogger(__name__)


def _spec_number(section: dict, key: str, conv, model_id: str):
    """Return ``conv(section[key])``, or None when absent / null / invalid.

    review 2026-07-06 finding 16: JSON ``null`` means "not set" (this
    module's own docstring shows ``"output_interval": null``) — it must not
    crash discovery with ``float(None)``. A non-numeric value logs a warning
    naming the model + field and is skipped so one bad entry doesn't abort
    the whole run.
    """
    if key not in section:
        return None
    raw = section[key]
    if raw is None:
        return None
    try:
        return conv(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Test '%s': ignoring non-numeric simulation/comparison value %s=%r",
            model_id,
            key,
            raw,
        )
        return None


def _entries(data: dict) -> list:
    """The spec's tests list, tolerating a missing/malformed key."""
    tests = data.get("tests")
    return tests if isinstance(tests, list) else []


def _find_entry_indices(data: dict, model_id: str) -> list[int]:
    """Indices of dict entries matching ``model_id``, warning on duplicates.

    review 2026-07-06 finding 51: duplicate model entries — the FIRST wins
    everywhere (discovery, patch_apply, and these edit helpers), each site
    warning so the user notices the dead entries.
    """
    matches = [
        i
        for i, e in enumerate(_entries(data))
        if isinstance(e, dict) and e.get("model") == model_id
    ]
    if len(matches) > 1:
        logger.warning(
            "test_spec has %d duplicate entries for model '%s'; "
            "using the first — remove the duplicates",
            len(matches),
            model_id,
        )
    return matches


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
    for i, entry in enumerate(tests_data):
        # review 2026-07-06 finding 53: a non-dict entry must be skipped with
        # a warning, not crash discovery with AttributeError.
        if not isinstance(entry, dict):
            logger.warning(
                "Skipping non-dict entry tests[%d] in %s: %r", i, spec_path, entry
            )
            continue
        model_id = entry.get("model")
        if not model_id:
            logger.warning("Skipping spec entry with no 'model' field")
            continue

        variables = entry.get("variables", [])
        if not isinstance(variables, list):
            logger.warning("'variables' must be a list for %s", model_id)
            variables = []

        parts = model_id.rsplit(".", 1)
        source_package = parts[0] if len(parts) > 1 else ""
        short_name = parts[-1]

        # Optional source-file field: path (relative to spec file) to a
        # simulation source. Generic across non-Modelica backends:
        #   "source" → the source file (.jl for Julia, .py for Python,
        #              .fmu also accepted here for symmetry).
        #   "fmu"    → legacy FMPy-specific alias (pre-D77); still
        #              supported but "source" is preferred for new tests.
        # Modelica tests omit this and source_file stays empty (the .mo
        # lives in the package discovered via source_package).
        source_file = Path("")
        fmu_rel = entry.get("fmu")
        source_rel = entry.get("source")
        if fmu_rel:
            fmu_path = (spec_path.parent / fmu_rel).resolve()
            if not fmu_path.exists():
                logger.warning(
                    "Test '%s' references missing FMU: %s", model_id, fmu_path
                )
            source_file = fmu_path
        elif source_rel:
            source_path_resolved = (spec_path.parent / source_rel).resolve()
            if not source_path_resolved.exists():
                logger.warning(
                    "Test '%s' references missing source file: %s",
                    model_id,
                    source_path_resolved,
                )
            source_file = source_path_resolved

        test = TestModel(
            model_id=model_id,
            source_file=source_file,
            source_package=source_package,
            short_name=short_name,
            n_vars=0,  # Will be resolved after simulation for pattern-based
            variable_patterns=variables,
            source="spec",
        )

        # Simulation settings. Fields the spec actually sets are stamped with
        # provenance "test_spec" here (review 2026-07-06 finding 57) — unset
        # fields stay None and get provenance "default" when
        # TestModel.finalize_defaults runs at the end of discover_tests.
        sim = entry.get("simulation", {})
        if not isinstance(sim, dict):
            logger.warning(
                "Test '%s': 'simulation' must be an object, got %r — ignored",
                model_id,
                sim,
            )
            sim = {}
        stop_time = _spec_number(sim, "stop_time", float, model_id)
        if stop_time is not None:
            test.stop_time = stop_time
            test.field_sources["stop_time"] = "test_spec"
        tolerance = _spec_number(sim, "tolerance", float, model_id)
        if tolerance is not None:
            test.tolerance = tolerance
            test.field_sources["tolerance"] = "test_spec"
        if sim.get("method") is not None:
            test.method = str(sim["method"])
            test.field_sources["method"] = "test_spec"
        n_intervals = _spec_number(sim, "number_of_intervals", int, model_id)
        if n_intervals is not None:
            test.number_of_intervals = n_intervals
            test.field_sources["number_of_intervals"] = "test_spec"
        output_interval = _spec_number(sim, "output_interval", float, model_id)
        if output_interval is not None:
            test.output_interval = output_interval
            test.field_sources["output_interval"] = "test_spec"
        timeout = _spec_number(sim, "timeout", int, model_id)
        if timeout is not None:
            test.timeout = timeout

        # Comparison settings
        comp = entry.get("comparison", {})
        if not isinstance(comp, dict):
            logger.warning(
                "Test '%s': 'comparison' must be an object, got %r — ignored",
                model_id,
                comp,
            )
            comp = {}
        comparison_tolerance = _spec_number(comp, "tolerance", float, model_id)
        if comparison_tolerance is not None:
            test.comparison_tolerance = comparison_tolerance
        if "variable_overrides" in comp:
            test.variable_overrides = comp["variable_overrides"]

        # Phase 3.2: optional MetricTree spec. Parse-only — Phase 3.3 wires
        # it into compare_test. Errors here are raised so a malformed spec
        # is loud at discovery, not silently ignored at compare time.
        metrics_raw = entry.get("metrics")
        if metrics_raw is not None:
            from ..comparison.tree_spec import parse_metric_tree

            test.metric_tree_spec = parse_metric_tree(
                metrics_raw,
                _path=f"tests[{model_id}].metrics",
            )

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
    data: dict = {"tests": []}
    if spec_path.exists():
        try:
            data = json.loads(spec_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
        if "tests" not in data:
            data["tests"] = []

    # Check if model already exists (non-dict entries skipped — finding 53;
    # duplicates: first wins with a warning — finding 51)
    matches = _find_entry_indices(data, model_id)

    if matches:
        if not overwrite:
            return False
        # review 2026-07-06 finding 52: overwrite replaces only
        # model + variables — hand-authored comparison/simulation/metrics
        # (and any unknown keys) must survive.
        entry = data["tests"][matches[0]]
        entry["model"] = model_id
        entry["variables"] = variables
    else:
        data["tests"].append({"model": model_id, "variables": variables})

    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True


def update_test_variables(
    spec_path: Path,
    model_id: str,
    additional_patterns: list[str],
) -> None:
    """Add variable patterns to an existing test entry, or create a new one."""
    data: dict = {"tests": []}
    if spec_path.exists():
        try:
            data = json.loads(spec_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
        if "tests" not in data:
            data["tests"] = []

    # Find existing entry (non-dict entries skipped, duplicates warned —
    # review 2026-07-06 findings 53/51)
    matches = _find_entry_indices(data, model_id)
    if matches:
        entry = data["tests"][matches[0]]
        existing = set(entry.get("variables", []))
        existing.update(additional_patterns)
        entry["variables"] = sorted(existing)
    else:
        data["tests"].append(
            {
                "model": model_id,
                "variables": sorted(additional_patterns),
            }
        )

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
    data: dict = {"tests": []}
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
    # (review 2026-07-06 finding 54: this used to REPLACE the section
    # wholesale, dropping variable_overrides — merge per the docstring:
    # keys present in update_data win, other keys survive, and
    # variable_overrides merge per-variable).
    matches = _find_entry_indices(data, model_id)
    if matches:
        entry = data["tests"][matches[0]]
        existing_comp = entry.get("comparison")
        if not isinstance(existing_comp, dict):
            existing_comp = {}
        entry["comparison"] = existing_comp
        for key, value in comparison.items():
            if key == "variable_overrides" and isinstance(value, dict):
                overrides = existing_comp.setdefault("variable_overrides", {})
                if not isinstance(overrides, dict):
                    overrides = {}
                    existing_comp["variable_overrides"] = overrides
                for var, override in value.items():
                    if isinstance(override, dict) and isinstance(
                        overrides.get(var), dict
                    ):
                        overrides[var].update(override)
                    else:
                        overrides[var] = override
            else:
                existing_comp[key] = value
    else:
        data["tests"].append(
            {
                "model": model_id,
                "comparison": comparison,
            }
        )

    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
