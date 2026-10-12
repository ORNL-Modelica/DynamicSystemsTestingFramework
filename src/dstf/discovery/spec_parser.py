"""Parse external test specification files (test_spec.json)."""

import json
import logging
from collections.abc import Iterable
from pathlib import Path

from ..config import ConfigError, baseline_points_refused
from ..storage.ref_index import _atomic_write_text
from . import spec_shape
from .test_registry import TestModel

logger = logging.getLogger(__name__)


class EntryKeyError(ValueError):
    """A key in a test entry that nothing reads: ``where`` it sits
    (``tests[Lib.M].simulation``) and the ``message`` that names it."""

    def __init__(self, where: str, message: str) -> None:
        self.where = where
        self.message = message
        super().__init__(f"{where}: {message}")


def check_entry_keys(entry: dict, model_id: str) -> None:
    """Refuse a key of ``entry`` that nothing reads (``strict_keys``): one
    of its own, of its ``simulation`` or ``comparison`` block, or of a flat
    override — which is one metric's params, as a leaf's are. The
    ``metrics`` tree and ``plots`` have their own parsers, each as strict.

    ``"simulaton": {"stop_time": 100}`` used to be skipped without a word
    and the test ran for the default second. The known keys are
    ``spec_shape``'s — the table ``export-schema`` publishes.

    And refuse a block that is no object where one is read — ``simulation``,
    ``comparison``, ``variable_overrides`` and each override in it; a
    ``null`` too: absent is the only "not set". ``"simulation":
    "stop_time=5"`` was a warning and a run on defaults,
    ``"variable_overrides": [1]`` and ``{"x": "tube"}`` a traceback — the
    second one at compare.

    Raises :class:`EntryKeyError`.
    """
    from ..comparison.sides import _as_json
    from ..comparison.strict_keys import unknown_field, unread_override_key

    here = f"tests[{model_id}]"

    def refuse(where: str, keys: Iterable[str], known: Iterable[str]) -> None:
        problem = unknown_field(keys, known)
        if problem is not None:
            raise EntryKeyError(where, problem)

    def block(where: str, holder: dict, key: str) -> dict | None:
        """``holder[key]`` when it is there; an object, or refused."""
        if key not in holder:
            return None
        value = holder[key]
        if not isinstance(value, dict):
            raise EntryKeyError(
                f"{where}.{key}", f"must be an object, got {_as_json(value)}"
            )
        return value

    refuse(here, entry, spec_shape.ENTRY)
    simulation = block(here, entry, "simulation")
    if simulation is not None:
        refuse(f"{here}.simulation", simulation, spec_shape.SIMULATION)
    comparison = block(here, entry, "comparison")
    if comparison is None:
        return
    refuse(f"{here}.comparison", comparison, spec_shape.COMPARISON)
    overrides = block(f"{here}.comparison", comparison, "variable_overrides")
    if overrides is None:
        return
    for variable, override in overrides.items():
        where = f"{here}.comparison.variable_overrides[{variable}]"
        if not isinstance(override, dict):
            raise EntryKeyError(where, f"must be an object, got {_as_json(override)}")
        found = unread_override_key(override)
        if found is not None:
            mode, problem = found
            raise EntryKeyError(where, f"{mode} · {variable}: {problem}")


def check_root_keys(data: object, label: str) -> list:
    """The spec document's ``tests`` list, or :class:`ConfigError` naming
    ``label`` when the document cannot hold one: a root that is no object,
    a root key nothing reads (``spec_shape.ROOT``), a ``tests`` that is no
    list. Each used to be a spec of NO tests — an error in the log, or
    nothing at all for ``{"Tests": [...]}`` — and the command went on
    without them, exit 0."""
    from ..comparison.sides import _as_json
    from ..comparison.strict_keys import unknown_field

    if not isinstance(data, dict):
        raise ConfigError(f"{label}: must be an object, got {_as_json(data)}")
    problem = unknown_field(data, spec_shape.ROOT)
    if problem is not None:
        raise ConfigError(f"{label}: {problem}")
    tests = data.get("tests", [])
    if not isinstance(tests, list):
        raise ConfigError(f"{label}: tests: must be a list, got {_as_json(tests)}")
    return tests


def _load_spec_for_edit(spec_path: Path) -> dict:
    """Load a spec about to be modified in place.

    A missing file is a new spec (``{"tests": []}``). An EXISTING file that
    cannot be read or parsed raises :class:`ConfigError`: the edit helpers
    below load-modify-write, so swallowing the parse error and carrying on
    with an empty dict replaced the user's hand-authored spec with a
    near-empty one on a single JSON typo.
    """
    if not spec_path.exists():
        return {"tests": []}
    try:
        # `utf-8-sig`, like every other reader of this file: a BOM is not
        # a reason to refuse a spec that otherwise parses.
        text = spec_path.read_text(encoding="utf-8-sig")
    except OSError as e:
        raise ConfigError(f"Cannot read test spec {spec_path}: {e}") from e
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ConfigError(f"Malformed JSON in test spec {spec_path}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"Test spec {spec_path} root must be a JSON object")
    data.setdefault("tests", [])
    if not isinstance(data["tests"], list):
        raise ConfigError(f"Test spec {spec_path}: 'tests' must be a list")
    return data


def _write_spec(spec_path: Path, data: dict) -> None:
    """Persist an edited spec atomically — an interrupted write must not
    leave the user's spec truncated."""
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(spec_path, json.dumps(data, indent=2) + "\n")


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
          },
          "baseline_points": 2000
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

    Reads the file, then hands the document to
    :func:`spec_models_from_document`; the authoring Session calls that
    directly with its in-memory document.
    """
    try:
        # `utf-8-sig`, like every other reader of this file.
        data = json.loads(spec_path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to read test spec %s: %s", spec_path, e)
        return []
    return spec_models_from_document(
        data, base_dir=spec_path.parent, label=str(spec_path)
    )


def spec_models_from_document(
    data: object, *, base_dir: Path, label: str
) -> list[TestModel]:
    """TestModels from an already-parsed spec document.

    ``base_dir`` anchors each entry's relative ``source``; ``label`` names the
    document in log messages and in the ``ConfigError`` a bad spec raises —
    a root that holds no ``tests`` list (:func:`check_root_keys`), an entry
    key nothing reads, a bad ``metrics`` tree. Not named ``test_...``:
    pytest collects such a name when a test module imports it.
    """
    tests_data = check_root_keys(data, label)

    tests = []
    for i, entry in enumerate(tests_data):
        # review 2026-07-06 finding 53: a non-dict entry must be skipped with
        # a warning, not crash discovery with AttributeError.
        if not isinstance(entry, dict):
            logger.warning(
                "Skipping non-dict entry tests[%d] in %s: %r", i, label, entry
            )
            continue
        model_id = entry.get("model")
        if not model_id:
            logger.warning("Skipping spec entry with no 'model' field")
            continue

        # A key nothing reads is an error that names it, like a malformed
        # tree: one "Config error: ..." line and exit 2.
        try:
            check_entry_keys(entry, model_id)
        except EntryKeyError as e:
            raise ConfigError(f"{label}: {e}") from e

        variables = entry.get("variables", [])
        if not isinstance(variables, list):
            logger.warning("'variables' must be a list for %s", model_id)
            variables = []

        parts = model_id.rsplit(".", 1)
        source_package = parts[0] if len(parts) > 1 else ""
        short_name = parts[-1]

        # Optional "source" field: path (relative to spec file) to the
        # simulation source — .jl for Julia, .py for Python, .fmu for FMPy.
        # Modelica tests omit this and source_file stays empty (the .mo
        # lives in the package discovered via source_package).
        source_file = Path("")
        source_rel = entry.get("source")
        if source_rel:
            source_path_resolved = (base_dir / source_rel).resolve()
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
        sim = entry.get("simulation", {})  # an object: `check_entry_keys`
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
        comp = entry.get("comparison", {})  # an object: `check_entry_keys`
        # The base tolerance is carried as written, number or not. One that
        # is no number (a lower/upper pair, a string, a list) refuses the
        # test at compare (`refused_base_tolerance`); dropped here like a simulation
        # value, the test was scored — and could pass — at the project's.
        raw_tolerance = comp.get("tolerance")
        if raw_tolerance is not None:
            from ..comparison.sides import is_base_tolerance

            test.comparison_tolerance = (
                float(raw_tolerance)
                if is_base_tolerance(raw_tolerance)
                else raw_tolerance
            )
        if "variable_overrides" in comp:
            test.variable_overrides = comp["variable_overrides"]

        # Phase 3.2: optional MetricTree spec. Parse-only — Phase 3.3 wires
        # it into compare_test. Errors here are raised so a malformed spec
        # is loud at discovery, not silently ignored at compare time.
        metrics_raw = entry.get("metrics")
        if metrics_raw is not None:
            from ..comparison.tree_spec import MetricSpecError, parse_metric_tree

            try:
                test.metric_tree_spec = parse_metric_tree(
                    metrics_raw,
                    _path=f"tests[{model_id}].metrics",
                )
            except MetricSpecError as e:
                # Still loud at discovery (never silently ignored), but as a
                # user error: one "Config error: ..." line and exit 2 rather
                # than a traceback out of the parser.
                raise ConfigError(f"{label}: {e}") from e

        if "plots" in entry:  # null is refused: absent is the only default
            from .plots import PlotsSpecError, parse_plots

            try:
                test.plots = parse_plots(
                    entry["plots"], path=f"tests[{model_id}].plots"
                )
            except PlotsSpecError as e:
                raise ConfigError(f"{label}: {e}") from e

        # The test's own baseline points (D153), over the project's. Refused
        # like `plots`, null included: a value that is neither form would
        # otherwise store this test's baseline at the project's setting.
        if "baseline_points" in entry:
            refused = baseline_points_refused(
                f"tests[{model_id}].baseline_points", entry["baseline_points"]
            )
            if refused is not None:
                raise ConfigError(f"{label}: {refused}")
            test.baseline_points = entry["baseline_points"]

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
    data = _load_spec_for_edit(spec_path)

    # Check if model already exists (non-dict entries skipped — finding 53;
    # duplicates: first wins with a warning — finding 51)
    matches = _find_entry_indices(data, model_id)

    if matches:
        if not overwrite:
            return False
        # review 2026-07-06 finding 52: overwrite replaces only
        # model + variables — hand-authored comparison/simulation/metrics
        # and notes must survive.
        entry = data["tests"][matches[0]]
        entry["model"] = model_id
        entry["variables"] = variables
    else:
        data["tests"].append({"model": model_id, "variables": variables})

    _write_spec(spec_path, data)
    return True


def update_test_variables(
    spec_path: Path,
    model_id: str,
    additional_patterns: list[str],
) -> None:
    """Add variable patterns to an existing test entry, or create a new one."""
    data = _load_spec_for_edit(spec_path)

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

    _write_spec(spec_path, data)
