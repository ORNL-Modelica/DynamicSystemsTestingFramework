"""A key nobody reads is an error that names it.

A ``range`` leaf written ``"max_val": 0.5`` (for ``max_value``) used to be
accepted by ``spec-update``, written to ``test_spec.json`` and scored as if
it had no upper bound: PASS. The same hole took a point row's
``tolerence``, a peak row's ``frq``, an override's ``tube_rell``, a
window's ``strt``, a leaf's ``agianst`` (then scored against the PRIMARY
baseline), an extra key on a combinator and an entry's ``simulaton``.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path

import pytest

from dstf.cli import main
from dstf.comparison import registry
from dstf.comparison.strict_keys import (
    metric_fields,
    row_fields,
    unknown_field,
    unread_param,
)
from dstf.comparison.tree_spec import MetricSpecError, parse_metric_tree
from dstf.config import ConfigError
from dstf.discovery.spec_parser import spec_models_from_document

REPO = Path(__file__).parent.parent
EXAMPLE = REPO / "examples" / "modelica" / "ModelicaTestingLib"
MODEL = "ModelicaTestingLib.Examples.MetricTreeTest"


# ---------------------------------------------------------------------------
# the one check
# ---------------------------------------------------------------------------


class TestTheCheck:
    def test_a_close_name_is_offered(self):
        assert (
            unknown_field(["min_value", "max_val"], ["min_value", "max_value"])
            == 'unknown field "max_val" — did you mean "max_value"?'
        )

    def test_with_no_close_name_the_fields_are_listed(self):
        assert (
            unknown_field(["fmu"], ["source", "model"])
            == 'unknown field "fmu"; the fields here are: model, source'
        )

    def test_the_start_of_a_known_name_is_close(self):
        assert (
            unknown_field(["min"], ["min_value", "max_value"])
            == 'unknown field "min" — did you mean "min_value"?'
        )

    def test_nothing_unknown_says_nothing(self):
        assert unknown_field(["a"], ["a", "b"]) is None
        assert unknown_field([], ["a"]) is None

    def test_a_metrics_fields_are_its_config_dataclasss(self):
        """Derived, not listed: adding a field to a config makes it a key."""
        for desc in registry.metrics():
            fields = {f.name for f in dataclasses.fields(desc.config_cls)}
            assert metric_fields(desc.name) == fields | {"tolerance", "quantity"}

    def test_a_tables_row_keys_are_its_columns(self):
        assert row_fields("points") == {
            "points": {"time", "value", "tolerance", "tolerance_mode", "time_tolerance"}
        }
        assert row_fields("tube") == {"tube_points": {"time", "upper", "lower"}}
        assert row_fields("event-timing") == {"events": {"time", "tolerance"}}
        assert row_fields("dominant-frequency") == {
            "peaks": {"freq", "tolerance", "tolerance_mode"}
        }
        assert row_fields("nrmse") == {}

    def test_an_unregistered_metric_is_not_this_checks_to_refuse(self):
        assert metric_fields("no-such-metric") is None
        assert unread_param("no-such-metric", {"anything": 1}) is None


# ---------------------------------------------------------------------------
# a leaf, its window, its rows; a combinator
# ---------------------------------------------------------------------------


def _refused(raw: dict) -> MetricSpecError:
    with pytest.raises(MetricSpecError) as caught:
        parse_metric_tree(raw)
    return caught.value


#: (the leaf as written, the sentence the tool shows beside it)
TYPOS = [
    (
        {
            "metric": "range",
            "variable": "unitTests.x[2]",
            "min_value": 0.0,
            "max_val": 0.5,
        },
        'range · unitTests.x[2]: unknown field "max_val" — did you mean "max_value"?',
    ),
    (
        {"metric": "nrmse", "variable": "x", "tolerence": 0.5},
        'nrmse · x: unknown field "tolerence" — did you mean "tolerance"?',
    ),
    (
        {"metric": "tube", "variable": "x", "tube_width_mode": "rel", "tube_rell": 0.5},
        'tube · x: unknown field "tube_rell" — did you mean "tube_rel"?',
    ),
    (
        {"metric": "nrmse", "variable": "x", "agianst": "openmodelica"},
        'nrmse · x: unknown field "agianst" — did you mean "against"?',
    ),
    (
        {"metric": "nrmse", "variable": "x", "window": {"strt": 1.0}},
        'nrmse · x: window: unknown field "strt" — did you mean "start"?',
    ),
    (
        {
            "metric": "points",
            "variable": "x",
            "points": [{"time": 0.0}, {"time": 1.0, "tolerence": 0.5}],
        },
        'points · x: points row 2: unknown field "tolerence" — did you mean '
        '"tolerance"?',
    ),
    (
        {
            "metric": "dominant-frequency",
            "variable": "x",
            "peaks": [{"frq": 1.0, "tolerance": 0.1}],
        },
        'dominant-frequency · x: peaks row 1: unknown field "frq" — did you mean '
        '"freq"?',
    ),
    (
        {
            "metric": "event-timing",
            "variable": "x",
            "events": [{"time": 1.0, "tolerance": 0.1, "tol": 1}],
        },
        # the start of a known key is close enough to offer
        'event-timing · x: events row 1: unknown field "tol" — did you mean '
        '"tolerance"?',
    ),
    (
        {
            "metric": "tube",
            "variable": "x",
            "tube_width_mode": "abs",
            "tube_points": [{"time": 0.0, "uper": 1.0, "lower": 0.0}],
        },
        'tube · x: tube_points row 1: unknown field "uper" — did you mean "upper"?',
    ),
    (
        # another metric's field is not this one's: nothing reads it here
        {"metric": "range", "variable": "x", "min_value": 0.0, "tube_rel": 0.1},
        'range · x: unknown field "tube_rel"; the fields here are: against, '
        "max_value, metric, min_value, quantity, tolerance, variable, window",
    ),
]


@pytest.mark.parametrize(
    "leaf,sentence",
    TYPOS,
    ids=[s.split(":")[0] + str(i) for i, (_, s) in enumerate(TYPOS)],
)
def test_a_misspelled_leaf_key_is_refused_with_its_sentence(leaf, sentence):
    assert _refused(leaf).message == sentence
    # and where it sits in a tree rides on the path, for the CLI
    nested = _refused(
        {"combinator": "and", "children": [{"metric": "nrmse", "variable": "a"}, leaf]}
    )
    assert nested.message == sentence
    assert nested.path.startswith("metrics.children[1]")


@pytest.mark.parametrize(
    "node,sentence",
    [
        (
            {
                "combinator": "and",
                "k": 1,
                "children": [{"metric": "nrmse", "variable": "x"}],
            },
            'and group: unknown field "k"; the fields here are: children, combinator',
        ),
        (
            {
                "combinator": "or",
                "childs": [],
                "children": [{"metric": "nrmse", "variable": "x"}],
            },
            'or group: unknown field "childs" — did you mean "children"?',
        ),
        (
            {
                "combinator": "k-of-n",
                "k": 1,
                "weights": [1.0],
                "children": [{"metric": "nrmse", "variable": "x"}],
            },
            'k-of-n group: unknown field "weights"; the fields here are: children, '
            "combinator, k",
        ),
        (
            {
                "combinator": "weighted",
                "weights": [1.0],
                "threshold": 1.0,
                "treshold": 2.0,
                "children": [{"metric": "nrmse", "variable": "x"}],
            },
            'weighted group: unknown field "treshold" — did you mean "threshold"?',
        ),
    ],
)
def test_a_combinator_takes_its_own_kinds_keys(node, sentence):
    assert _refused(node).message == sentence


def test_every_key_a_builtin_reads_is_still_taken():
    """Each builtin with every field of its config, a window, a baseline
    and every column of its row tables: nothing the scorer reads is refused."""
    rows = {
        "points": [
            {
                "time": 0.0,
                "value": 1.0,
                "tolerance": {"lower": 0.1, "upper": 0.2},
                "tolerance_mode": "abs",
                "time_tolerance": 0.1,
            }
        ],
        "tube_points": [{"time": 0.0, "upper": 1.0, "lower": 0.5}],
        "events": [{"time": 1.0, "tolerance": 0.1}],
        "peaks": [{"freq": 1.0, "tolerance": 0.1, "tolerance_mode": "rel"}],
    }
    for desc in registry.metrics():
        leaf = {
            "metric": desc.name,
            "variable": "x",
            "against": "primary",
            "window": {"start": 0.0, "end": 1.0},
            "tolerance": 0.5,
        }
        for f in dataclasses.fields(desc.config_cls):
            leaf[f.name] = rows.get(f.name)  # null where it is not a table
        parsed = parse_metric_tree(leaf)
        assert set(parsed.params) == set(leaf) - {
            "metric",
            "variable",
            "against",
            "window",
        }


def test_quantity_on_a_trajectory_metric_stays_the_documented_inert_param():
    """docs/extensibility.md, the Metric contract: "an unrecognized
    `quantity` stays an inert param — a trajectory metric keeps scoring
    trajectories". Kept by name; no other key is inert."""
    for desc in registry.metrics():
        leaf = parse_metric_tree(
            {"metric": desc.name, "variable": "x", "quantity": {"reduce": "max"}}
        )
        assert leaf.params == {"quantity": {"reduce": "max"}}
    with pytest.raises(MetricSpecError, match='unknown field "quantities"'):
        parse_metric_tree({"metric": "nrmse", "variable": "x", "quantities": "a"})


class TestAPlugInMetric:
    """A metric registered with its own config class is checked against
    ITS fields; one whose config is no dataclass is not checked."""

    @pytest.fixture(autouse=True)
    def _restore(self):
        yield
        registry._reset_for_tests()

    def _register(self, config_cls):
        from dstf.comparison.modes import NrmseConfig, NrmseMode

        registry.register_metric(
            registry.MetricDescriptor(
                name="energy",
                config_cls=config_cls,
                build=lambda override, tolerance: NrmseMode(NrmseConfig()),
            )
        )

    def test_its_own_fields_and_rows(self):
        @dataclasses.dataclass(frozen=True)
        class EnergyConfig:
            band: float = 1.0
            marks: list | None = dataclasses.field(
                default=None,
                metadata={"columns": (("at", "float"), ("limit", "float?"))},
            )

        self._register(EnergyConfig)
        parse_metric_tree(
            {
                "metric": "energy",
                "variable": "E",
                "band": 2.0,
                "marks": [{"at": 1.0, "limit": 2.0}],
            }
        )
        assert _refused({"metric": "energy", "variable": "E", "bnd": 2.0}).message == (
            'energy · E: unknown field "bnd" — did you mean "band"?'
        )
        assert _refused(
            {"metric": "energy", "variable": "E", "marks": [{"at": 1.0, "limt": 2.0}]}
        ).message == (
            'energy · E: marks row 1: unknown field "limt" — did you mean "limit"?'
        )
        # a builtin's field is not this metric's
        assert (
            "max_value"
            in _refused({"metric": "energy", "variable": "E", "max_value": 1.0}).message
        )

    def test_a_config_that_is_no_dataclass_is_not_checked(self):
        class Opaque:
            pass

        self._register(Opaque)
        leaf = parse_metric_tree({"metric": "energy", "variable": "E", "whatever": 1})
        assert leaf.params == {"whatever": 1}
        # the leaf's structural keys are the parser's own, whatever the metric
        assert (
            'unknown field "strt"'
            in _refused(
                {"metric": "energy", "variable": "E", "window": {"strt": 0}}
            ).message
        )


# ---------------------------------------------------------------------------
# a test entry: its own keys, its simulation and comparison blocks, its
# flat overrides
# ---------------------------------------------------------------------------


def _load(entry: dict):
    return spec_models_from_document(
        {"tests": [entry]}, base_dir=Path("."), label="test_spec.json"
    )


def _config_error(entry: dict) -> str:
    with pytest.raises(ConfigError) as caught:
        _load(entry)
    return str(caught.value)


@pytest.mark.parametrize(
    "entry,sentence",
    [
        (
            {"model": "L.M", "variables": ["x"], "simulaton": {"stop_time": 5}},
            'test_spec.json: tests[L.M]: unknown field "simulaton" — did you mean '
            '"simulation"?',
        ),
        (
            {"model": "L.M", "comparision": {"tolerance": 0.1}},
            'test_spec.json: tests[L.M]: unknown field "comparision" — did you mean '
            '"comparison"?',
        ),
        (
            {"model": "L.M", "simulation": {"stop_tme": 5}},
            'test_spec.json: tests[L.M].simulation: unknown field "stop_tme" — did '
            'you mean "stop_time"?',
        ),
        (
            {"model": "L.M", "comparison": {"tolerence": 0.1}},
            'test_spec.json: tests[L.M].comparison: unknown field "tolerence" — did '
            'you mean "tolerance"?',
        ),
        (
            {
                "model": "L.M",
                "comparison": {
                    "variable_overrides": {
                        "x": {
                            "mode": "tube",
                            "tube_width_mode": "rel",
                            "tube_rell": 0.02,
                        }
                    }
                },
            },
            "test_spec.json: tests[L.M].comparison.variable_overrides[x]: tube · x: "
            'unknown field "tube_rell" — did you mean "tube_rel"?',
        ),
        (
            {
                "model": "L.M",
                "comparison": {"variable_overrides": {"x": {"tolerence": 1}}},
            },
            "test_spec.json: tests[L.M].comparison.variable_overrides[x]: nrmse · x: "
            'unknown field "tolerence" — did you mean "tolerance"?',
        ),
        (
            {
                "model": "L.M",
                "comparison": {
                    "variable_overrides": {
                        "x": {"mode": "points", "points": [{"time": 1, "valu": 2}]}
                    }
                },
            },
            "test_spec.json: tests[L.M].comparison.variable_overrides[x]: points · x: "
            'points row 1: unknown field "valu" — did you mean "value"?',
        ),
        (
            {
                "model": "L.M",
                "metrics": {"metric": "range", "variable": "x", "max_val": 1},
            },
            "test_spec.json: tests[L.M].metrics: range · x: unknown field "
            '"max_val" — did you mean "max_value"?',
        ),
    ],
)
def test_a_misspelled_entry_key_is_a_config_error_that_names_it(entry, sentence):
    assert _config_error(entry) == sentence


def test_the_entry_keys_that_are_read_and_the_notes_are_taken():
    """Every key the parser reads, and `description` / `info` / `metadata`
    — the documented place for a person's notes — on the entry and on its
    comparison block."""
    (test,) = _load(
        {
            "model": "L.M",
            "source": "m.py",
            "variables": ["x"],
            "simulation": {
                "stop_time": 5,
                "tolerance": 1e-6,
                "method": "Dassl",
                "number_of_intervals": 100,
                "output_interval": None,
                "timeout": 60,
            },
            "comparison": {
                "tolerance": 0.1,
                "variable_overrides": {
                    "x": {"mode": "range", "min_value": 0.0, "tolerance": 0.5},
                    "y": {"tolerance": 0.2},
                },
                "description": "why this is loose",
                "info": "see the ticket",
                "metadata": {"anything": {"goes": "here"}},
            },
            "metrics": {"metric": "nrmse", "variable": "x"},
            "plots": [],
            "baseline_points": "all",
            "description": "a note",
            "info": "another",
            "metadata": {"owner": "me"},
        }
    )
    assert test.stop_time == 5 and test.variable_overrides["y"] == {"tolerance": 0.2}


def test_an_override_with_no_mode_may_carry_what_default_points_reads():
    """`--default-points` scores a mode-less override as `points`: its
    `points` table is read then, so it is no unknown key."""
    _load(
        {
            "model": "L.M",
            "comparison": {
                "variable_overrides": {
                    "x": {"points": [{"time": 1.0}], "tolerance": 0.1}
                }
            },
        }
    )
    assert "tube_rel" in _config_error(
        {"model": "L.M", "comparison": {"variable_overrides": {"x": {"tube_rel": 0.1}}}}
    )


def test_an_override_naming_no_registered_mode_is_left_to_the_comparison():
    """It fails that variable loudly at compare, as before; its keys are
    not this check's to judge."""
    (test,) = _load(
        {
            "model": "L.M",
            "comparison": {
                "variable_overrides": {"x": {"mode": "Tube", "tube_rell": 1}}
            },
        }
    )
    assert test.variable_overrides["x"]["mode"] == "Tube"


# ---------------------------------------------------------------------------
# no shipped file breaks
# ---------------------------------------------------------------------------

_SHIPPED = [
    p
    for root in ("examples", "tests/fixtures")
    for p in sorted((REPO / root).rglob("*.json"))
    if "testing_output" not in p.parts
]


def _named(name: str) -> list[Path]:
    return [p for p in _SHIPPED if p.name == name]


#: The shipped projects: each example's testing.json. (`tests/fixtures/
#: testing.json` is a template the fixtures copy beside a library — where it
#: lies its `source_path` names nothing, so it opens no project; its
#: `test_spec.json` is walked with the others.)
_PROJECTS = [p for p in _named("testing.json") if "examples" in p.parts]


class TestNoShippedFileBreaks:
    def test_there_are_files_to_walk(self):
        assert len(_named("test_spec.json")) >= 5
        assert len(_PROJECTS) >= 4
        assert len([p for p in _SHIPPED if p.name.startswith("ref_")]) >= 50

    @pytest.mark.parametrize(
        "path", _named("test_spec.json"), ids=lambda p: str(p.relative_to(REPO))
    )
    def test_every_test_spec_loads_with_every_entry(self, path):
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        models = spec_models_from_document(data, base_dir=path.parent, label=str(path))
        assert [m.model_id for m in models] == [e["model"] for e in data["tests"]]

    @pytest.mark.parametrize("path", _PROJECTS, ids=lambda p: str(p.relative_to(REPO)))
    def test_every_testing_json_discovers_its_tests(self, path, tmp_path):
        """Through the real entry point, read-only: `discover` over each
        shipped project finds its tests (a spec that no longer loaded
        would be a `Config error`, exit 2)."""
        from dstf.config import Config
        from dstf.discovery.test_registry import discover_tests

        tests = discover_tests(Config(config_file=path, work_dir=tmp_path))
        assert tests, path

    @pytest.mark.parametrize(
        "path",
        [p for p in _SHIPPED if p.name.startswith("ref_")],
        ids=lambda p: str(p.relative_to(REPO)),
    )
    def test_every_reference_json_holds_only_keys_that_are_read(self, path):
        """A baseline keeps the comparison settings it was accepted with,
        and they are merged under the spec's at compare. They are not
        refused at load — and no shipped one would be: each stored override
        builds, and holds no key its mode does not read."""
        from dstf.comparison.modes import resolve_mode

        ref = json.loads(path.read_text(encoding="utf-8"))
        comparison = ref.get("comparison") or {}
        for variable, override in (comparison.get("variable_overrides") or {}).items():
            mode = resolve_mode(override, override.get("tolerance", 1e-4))
            params = {k: v for k, v in override.items() if k != "mode"}
            assert unread_param(mode.name, params) is None, (variable, override)


# ---------------------------------------------------------------------------
# the commands: spec-update refuses and writes nothing; run / compare /
# discover stop at the spec
# ---------------------------------------------------------------------------


@pytest.fixture
def example(tmp_path) -> Path:
    """A copy of the bundled Modelica example; returns its testing.json."""
    lib = tmp_path / "ModelicaTestingLib"
    shutil.copytree(EXAMPLE, lib, ignore=shutil.ignore_patterns("testing_output"))
    return lib / "Resources" / "ReferenceResults" / "testing.json"


def _patch(tmp_path: Path, *ops: dict) -> Path:
    path = tmp_path / "patch.json"
    path.write_text(
        json.dumps({"patches": [{"model": MODEL, "patch": list(ops)}]}),
        encoding="utf-8",
    )
    return path


def _add_leaf(leaf: dict) -> dict:
    return {"op": "add", "path": "/metrics/children/-", "value": leaf}


class TestSpecUpdateRefuses:
    def _refused(self, example, patch, capsys) -> str:
        spec = example.parent / "test_spec.json"
        before = spec.read_bytes()
        capsys.readouterr()
        assert main(["--config", str(example), "spec-update", str(patch)]) == 1
        assert spec.read_bytes() == before
        out = capsys.readouterr().out
        assert out.startswith("Patch failed: ") and "Applied" not in out
        return out.strip()

    def test_the_misspelled_bound_that_used_to_pass(self, example, tmp_path, capsys):
        """`min_value: 0.0, max_val: 0.5` — applied, and scored PASS with no
        upper bound, before this."""
        patch = _patch(
            tmp_path,
            _add_leaf(
                {
                    "metric": "range",
                    "variable": "unitTests.x[2]",
                    "min_value": 0.0,
                    "max_val": 0.5,
                }
            ),
        )
        assert self._refused(example, patch, capsys).endswith(
            'range · unitTests.x[2]: unknown field "max_val" — did you mean "max_value"?'
        )

    def test_the_misspelled_only_bound(self, example, tmp_path, capsys):
        """The second evidence patch: `max_val` alone."""
        patch = _patch(
            tmp_path,
            _add_leaf(
                {"metric": "range", "variable": "unitTests.x[2]", "max_val": 1.01}
            ),
        )
        assert 'unknown field "max_val"' in self._refused(example, patch, capsys)

    def test_a_misspelled_override_key(self, example, tmp_path, capsys):
        patch = _patch(
            tmp_path,
            {
                "op": "add",
                "path": "/comparison",
                "value": {
                    "variable_overrides": {
                        "unitTests.x[1]": {"mode": "tube", "tube_rell": 0.1}
                    }
                },
            },
        )
        assert self._refused(example, patch, capsys).endswith(
            'tube · unitTests.x[1]: unknown field "tube_rell" — did you mean "tube_rel"?'
        )

    def test_a_misspelled_comparison_key(self, example, tmp_path, capsys):
        patch = _patch(
            tmp_path, {"op": "add", "path": "/comparison", "value": {"tolerence": 0.1}}
        )
        assert 'comparison: unknown field "tolerence"' in self._refused(
            example, patch, capsys
        )

    def test_the_validators_own_refusal_is_as_it_was(self, example, tmp_path, capsys):
        """The third evidence patch: a tree of warnings only."""
        patch = _patch(
            tmp_path,
            {
                "op": "replace",
                "path": "/metrics",
                "value": {
                    "combinator": "warn",
                    "children": [
                        {
                            "metric": "nrmse",
                            "variable": "unitTests.x[1]",
                            "tolerance": 0.001,
                        }
                    ],
                },
            },
        )
        assert "validator rejected the patched tree" in self._refused(
            example, patch, capsys
        )

    def test_a_well_spelled_patch_still_applies(self, example, tmp_path, capsys):
        patch = _patch(
            tmp_path,
            _add_leaf(
                {
                    "metric": "range",
                    "variable": "unitTests.x[2]",
                    "min_value": 0.0,
                    "max_value": 0.5,
                }
            ),
        )
        assert main(["--config", str(example), "spec-update", str(patch)]) == 0
        assert "Applied 1 op(s)" in capsys.readouterr().out
        spec = json.loads(
            (example.parent / "test_spec.json").read_text(encoding="utf-8")
        )
        (entry,) = [e for e in spec["tests"] if e["model"] == MODEL]
        assert entry["metrics"]["children"][-1]["max_value"] == 0.5


@pytest.mark.parametrize("command", ["discover", "run", "compare"])
def test_a_spec_holding_an_unknown_key_stops_the_command(example, capsys, command):
    """`Config error: …` and exit 2 — what a malformed tree already gets —
    before anything is simulated or scored."""
    spec_path = example.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    (entry,) = [e for e in spec["tests"] if e["model"] == MODEL]
    entry["metrics"]["children"].append(
        {
            "metric": "range",
            "variable": "unitTests.x[2]",
            "min_value": 0.0,
            "max_val": 0.5,
        }
    )
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    extra = [] if command == "discover" else ["--no-browser"]
    assert main(["--config", str(example), command, *extra]) == 2
    err = capsys.readouterr().err
    assert err.startswith("Config error: ")
    assert err.strip().endswith(
        f"tests[{MODEL}].metrics.children[3]: range · unitTests.x[2]: unknown field "
        '"max_val" — did you mean "max_value"?'
    )


# ---------------------------------------------------------------------------
# the exported schema says what the parser enforces
# ---------------------------------------------------------------------------


class TestTheSchemaIsAsStrictAsTheParser:
    @pytest.fixture(scope="class")
    def schema(self):
        from dstf.reporting.schema_export import build_schema

        return build_schema()

    def test_additional_properties_is_false_wherever_the_parser_is_strict(self, schema):
        defs = schema["$defs"]
        entry = defs["test_entry"]
        assert entry["additionalProperties"] is False
        assert entry["properties"]["simulation"]["additionalProperties"] is False
        assert entry["properties"]["comparison"]["additionalProperties"] is False
        overrides = entry["properties"]["comparison"]["properties"][
            "variable_overrides"
        ]
        assert overrides["additionalProperties"] == {"$ref": "#/$defs/override"}
        assert defs["combinator"]["additionalProperties"] is False
        for desc in registry.metrics():
            name = desc.name.replace("-", "_")
            assert defs[f"mode_{name}"]["additionalProperties"] is False, name
            assert defs[f"override_{name}"]["additionalProperties"] is False, name
        assert (
            defs["mode_nrmse"]["properties"]["window"]["additionalProperties"] is False
        )
        points = defs["mode_points"]["properties"]["points"]
        assert points["items"]["additionalProperties"] is False
        assert set(points["items"]["properties"]) == row_fields("points")["points"]

    def test_the_entry_keys_are_the_parsers(self, schema):
        """One table (`discovery.spec_shape`), read by both."""
        from dstf.discovery import spec_shape

        entry = schema["$defs"]["test_entry"]["properties"]
        assert set(entry) == set(spec_shape.ENTRY)
        assert set(entry["simulation"]["properties"]) == set(spec_shape.SIMULATION)
        assert set(entry["comparison"]["properties"]) == set(spec_shape.COMPARISON)

    @pytest.mark.parametrize(
        "path", _named("test_spec.json"), ids=lambda p: str(p.relative_to(REPO))
    )
    def test_every_shipped_spec_validates(self, schema, path):
        jsonschema = pytest.importorskip("jsonschema")
        jsonschema.validate(json.loads(path.read_text(encoding="utf-8-sig")), schema)

    @pytest.mark.parametrize(
        "leaf,sentence", TYPOS, ids=[str(i) for i in range(len(TYPOS))]
    )
    def test_what_the_parser_refuses_on_a_leaf_the_schema_refuses(
        self, schema, leaf, sentence
    ):
        jsonschema = pytest.importorskip("jsonschema")
        spec = {"tests": [{"model": "L.M", "metrics": leaf}]}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(spec, schema)

    @pytest.mark.parametrize(
        "entry",
        [
            {"model": "L.M", "simulaton": {}},
            {"model": "L.M", "simulation": {"stop_tme": 5}},
            {"model": "L.M", "comparison": {"tolerence": 0.1}},
            {
                "model": "L.M",
                "comparison": {
                    "variable_overrides": {"x": {"mode": "tube", "tube_rell": 1}}
                },
            },
            {
                "model": "L.M",
                "comparison": {"variable_overrides": {"x": {"tolerence": 1}}},
            },
            {
                "model": "L.M",
                "metrics": {
                    "combinator": "and",
                    "k": 1,
                    "children": [{"metric": "nrmse", "variable": "x"}],
                },
            },
        ],
    )
    def test_what_the_parser_refuses_on_an_entry_the_schema_refuses(
        self, schema, entry
    ):
        jsonschema = pytest.importorskip("jsonschema")
        with pytest.raises(ConfigError):
            _load(entry)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"tests": [entry]}, schema)

    def test_what_the_parser_takes_the_schema_takes(self, schema):
        jsonschema = pytest.importorskip("jsonschema")
        entry = {
            "model": "L.M",
            "variables": ["x"],
            "simulation": {"stop_time": 5, "timeout": 30},
            "comparison": {
                "tolerance": 0.1,
                "variable_overrides": {
                    "x": {"mode": "range", "min_value": 0.0, "tolerance": 0.5},
                    "y": {"tolerance": 0.2},
                    "z": {"points": [{"time": 1.0}]},
                },
                "metadata": {"anything": 1},
            },
            "metrics": {
                "combinator": "k-of-n",
                "k": 1,
                "children": [
                    {"metric": "nrmse", "variable": "x", "quantity": "inert"},
                    {
                        "metric": "points",
                        "variable": "x",
                        "points": [
                            {"time": 1.0, "tolerance": {"lower": 0.1, "upper": 0.2}}
                        ],
                    },
                ],
            },
            "description": "a note",
        }
        _load(entry)
        jsonschema.validate({"tests": [entry]}, schema)


# ---------------------------------------------------------------------------
# the authoring tool: a stage is refused with the sentence, and nothing is
# left pending
# ---------------------------------------------------------------------------


class TestTheToolRefusesToStage:
    @pytest.fixture
    def session(self, tmp_path):
        pytest.importorskip("nicegui")
        from dstf.authoring.session import Session
        from family_project import family_project

        cfg, work = family_project(tmp_path, 3, tracked=(1, 2), fail=())
        session = Session()
        session.open(cfg, work_dir=work)
        return session

    def _refused(self, session, op) -> str:
        from dstf.authoring.errors import user_text
        from dstf.discovery.patch_apply import PatchError

        before = list(session.spec.pending)
        with pytest.raises(PatchError) as caught:
            session.stage([op])
        assert session.spec.pending == before
        return user_text(caught.value)

    def test_a_misspelled_leaf_key(self, session):
        from dstf.authoring.spec_document import PatchOp
        from test_session import A

        tree = {
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "x"},
                {
                    "metric": "range",
                    "variable": "pipe.T[1]",
                    "min_value": 0,
                    "max_val": 9,
                },
            ],
        }
        assert self._refused(session, PatchOp(A, "add", "/metrics", tree)) == (
            'range · pipe.T[1]: unknown field "max_val" — did you mean "max_value"?'
        )

    def test_a_misspelled_override_key(self, session):
        from dstf.authoring.spec_document import PatchOp
        from test_session import A

        op = PatchOp(
            A,
            "add",
            "/comparison/variable_overrides",
            {"x": {"mode": "tube", "tube_width_mode": "rel", "tube_rell": 0.1}},
        )
        assert self._refused(session, op) == (
            'tube · x: unknown field "tube_rell" — did you mean "tube_rel"?'
        )
