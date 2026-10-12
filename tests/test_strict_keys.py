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
#: An entry of the same example with no `metrics` tree: its flat overrides score.
FLAT = "ModelicaTestingLib.Examples.TubeToleranceTest"


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

    @pytest.mark.parametrize(
        "typed,names,meant",
        [
            # the slips a hint is for: a letter dropped, added, replaced,
            # two swapped, another case
            ("max_val", ["min_value", "max_value"], "max_value"),
            ("tolerence", ["tolerance", "time_tolerance"], "tolerance"),
            ("frq", ["freq", "tolerance", "tolerance_mode"], "freq"),
            ("agianst", ["against", "metric", "variable", "window"], "against"),
            ("tiem", ["time", "value", "tolerance"], "time"),  # ONE slip: a swap
            ("simulaton", ["simulation", "comparison", "metrics"], "simulation"),
            ("uper", ["lower", "upper"], "upper"),
            ("contxt", ["var", "context"], "context"),
            ("K", ["k", "children", "combinator"], "k"),
            ("Tests", ["tests", "$schema"], "tests"),
            # the start of one name: as typed, or but for its last letter
            ("min", ["min_value", "max_value"], "min_value"),
            ("childs", ["children", "combinator"], "children"),
            # and what read as close and was not: a hint to the wrong field
            ("start_time", ["stop_time", "tolerance", "method"], None),
            ("notes", ["plots", "model", "metrics", "variables"], None),
            ("simulate_only", ["simulation", "comparison"], None),
            ("tolerance_mode", ["tolerance", "points", "time_tolerance"], None),
            # four fields start with `tube`: none of them is THE one
            ("tube", ["tube_abs", "tube_rel", "tube_points", "tube_min_width"], None),
            # as far from one bound as from the other
            ("m_value", ["min_value", "max_value"], None),
            # one letter is no near miss of another
            ("n", ["k", "children"], None),
        ],
    )
    def test_only_a_near_miss_is_suggested(self, typed, names, meant):
        from dstf.comparison.strict_keys import near_miss

        assert near_miss(typed, names) == meant
        said = unknown_field([typed], names)
        if meant is None:
            assert said == (
                f'unknown field "{typed}"; the fields here are: '
                + ", ".join(sorted(names))
            )
        else:
            assert said == f'unknown field "{typed}" — did you mean "{meant}"?'

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
        and they are merged under the spec's at compare — where a key
        nothing reads stops the command (`TestAStoredBaselinesOverride`).
        No shipped one holds one, and each stored override builds."""
        from dstf.comparison.modes import resolve_mode

        ref = json.loads(path.read_text(encoding="utf-8"))
        comparison = ref.get("comparison") or {}
        for variable, override in (comparison.get("variable_overrides") or {}).items():
            mode = resolve_mode(override, override.get("tolerance", 1e-4))
            params = {k: v for k, v in override.items() if k != "mode"}
            assert unread_param(mode.name, params) is None, (variable, override)


# ---------------------------------------------------------------------------
# one level out: the spec's root, a block that is no object, the keys inside
# a quantity and a tolerance pair, a stored baseline's override
# ---------------------------------------------------------------------------


def _root_error(document: object) -> str:
    with pytest.raises(ConfigError) as caught:
        spec_models_from_document(document, base_dir=Path("."), label="test_spec.json")
    return str(caught.value)


class TestTheSpecsRoot:
    def test_a_root_key_nothing_reads(self):
        """`{"Tests": [...]}` was a spec of no tests, with no word said."""
        assert _root_error({"Tests": [{"model": "L.M", "variables": ["x"]}]}) == (
            'test_spec.json: unknown field "Tests" — did you mean "tests"?'
        )

    def test_a_key_beside_tests(self):
        assert _root_error({"tests": [], "defaults": {}}) == (
            'test_spec.json: unknown field "defaults"; the fields here are: '
            "$schema, tests"
        )

    def test_a_root_that_is_no_object(self):
        assert _root_error([{"model": "L.M"}]) == (
            'test_spec.json: must be an object, got [{"model": "L.M"}]'
        )

    def test_a_tests_that_is_no_list(self):
        assert _root_error({"tests": {"L.M": {}}}) == (
            'test_spec.json: tests: must be a list, got {"L.M": {}}'
        )

    def test_the_schema_pointer_and_an_empty_root_are_taken(self):
        assert _load_root({"$schema": "./test_spec.schema.json", "tests": []}) == []
        assert _load_root({}) == []


def _load_root(document: object):
    return spec_models_from_document(
        document, base_dir=Path("."), label="test_spec.json"
    )


@pytest.mark.parametrize(
    "entry,sentence",
    [
        (
            {"model": "L.M", "simulation": "stop_time=5"},
            'tests[L.M].simulation: must be an object, got "stop_time=5"',
        ),
        (
            {"model": "L.M", "simulation": None},
            "tests[L.M].simulation: must be an object, got null",
        ),
        (
            {"model": "L.M", "comparison": [0.01]},
            "tests[L.M].comparison: must be an object, got [0.01]",
        ),
        (
            {"model": "L.M", "comparison": None},
            "tests[L.M].comparison: must be an object, got null",
        ),
        (
            {"model": "L.M", "comparison": {"variable_overrides": [1]}},
            "tests[L.M].comparison.variable_overrides: must be an object, got [1]",
        ),
        (
            {"model": "L.M", "comparison": {"variable_overrides": {"x": "tube"}}},
            'tests[L.M].comparison.variable_overrides[x]: must be an object, got "tube"',
        ),
    ],
    ids=[
        "simulation-a-string",
        "simulation-null",
        "comparison-a-list",
        "comparison-null",
        "overrides-a-list",
        "an-override-a-string",
    ],
)
def test_a_block_that_is_no_object_is_a_config_error(entry, sentence):
    """Each was a warning and a run on defaults, or a traceback — the
    string override's at compare."""
    assert _config_error(entry) == f"test_spec.json: {sentence}"


def _scalar(**params) -> dict:
    return {"metric": "scalar", "variable": "x", **params}


@pytest.mark.parametrize(
    "leaf,sentence",
    [
        (
            _scalar(quantity={"reduse": "max"}),
            'scalar · x: quantity: unknown field "reduse" — did you mean "reduce"?',
        ),
        (
            _scalar(quantity={"reduce": "max", "over": "x"}),
            'scalar · x: quantity: unknown field "over"; the fields here are: '
            "expr, reduce",
        ),
        (
            _scalar(quantity={"reduce": "max"}, tolerance={"lower": 0.1, "uper": 0.2}),
            'scalar · x: tolerance: unknown field "uper" — did you mean "upper"?',
        ),
        (
            {
                "metric": "tube",
                "variable": "x",
                "tube_width_mode": "rel",
                "tube_rel": {"lower": 0.1, "upper": 0.2, "middle": 0},
            },
            'tube · x: tube_rel: unknown field "middle"; the fields here are: '
            "lower, upper",
        ),
        (
            {
                "metric": "points",
                "variable": "x",
                "points": [
                    {"time": 1},
                    {"time": 2, "tolerance": {"lowr": 1, "upper": 2}},
                ],
            },
            'points · x: points row 2: tolerance: unknown field "lowr" — did you mean '
            '"lower"?',
        ),
    ],
    ids=["quantity-reduse", "quantity-over", "pair-uper", "pair-third-key", "row-pair"],
)
def test_a_key_inside_a_quantity_or_a_pair_is_refused_at_parse(leaf, sentence):
    """`{"reduce": "max", "over": "x"}` scored the max and dropped `over`;
    the others were found at compare, as a criterion that failed."""
    tree = {"combinator": "and", "children": [leaf]}
    error = _refused(tree)
    assert error.message == sentence
    # and on the flat path: the same params as an override
    override = {k: v for k, v in leaf.items() if k not in ("metric", "variable")}
    override["mode"] = leaf["metric"]
    entry = {"model": "L.M", "comparison": {"variable_overrides": {"x": override}}}
    assert _config_error(entry) == (
        f"test_spec.json: tests[L.M].comparison.variable_overrides[x]: {sentence}"
    )


def test_a_pair_and_a_quantity_as_they_are_read_are_taken():
    tree = {
        "combinator": "and",
        "children": [
            _scalar(quantity={"reduce": "max"}, tolerance={"lower": 0.1, "upper": 0.2}),
            _scalar(quantity="CPUtime.final"),
            # inert on a trajectory metric, of any shape: not looked into
            {"metric": "nrmse", "variable": "x", "quantity": {"anything": 1}},
        ],
    }
    assert len(parse_metric_tree(tree).children) == 3


class TestAStoredBaselinesOverride:
    """The reference JSON keeps the comparison settings its baseline was
    accepted with, and the flat path merges them under the spec's."""

    def _project(self, tmp_path, override):
        from test_session import D, _project

        cfg, work = _project(tmp_path)
        (ref,) = [
            p
            for p in cfg.parent.rglob("ref_*.json")
            if json.loads(p.read_text(encoding="utf-8"))["model_id"] == D
        ]
        data = json.loads(ref.read_text(encoding="utf-8"))
        data["comparison"] = {"variable_overrides": {"x": override}}
        ref.write_text(json.dumps(data), encoding="utf-8")
        return cfg, work, ref

    def _compare(self, cfg, work, capsys) -> tuple[int, str]:
        capsys.readouterr()
        code = main(["--config", str(cfg), "compare", "--work-dir", str(work)])
        return code, capsys.readouterr().err

    def test_a_key_nothing_reads_stops_the_compare_and_names_the_file(
        self, tmp_path, capsys
    ):
        """It scored as a range with no upper bound: PASS at any value."""
        cfg, work, ref = self._project(
            tmp_path, {"mode": "range", "min_value": -1, "max_val": 0.5}
        )
        code, err = self._compare(cfg, work, capsys)
        assert code == 2
        assert err.strip().splitlines()[-1] == (
            f"Config error: {ref}: comparison.variable_overrides[x]: range · x: "
            'unknown field "max_val" — did you mean "max_value"?'
        )

    def test_an_override_that_is_no_object(self, tmp_path, capsys):
        cfg, work, ref = self._project(tmp_path, "tube")
        code, err = self._compare(cfg, work, capsys)
        assert code == 2
        assert err.strip().splitlines()[-1] == (
            f"Config error: {ref}: comparison.variable_overrides[x]: must be an "
            'object, got "tube"'
        )

    def test_one_that_is_read_scores_as_it_did(self, tmp_path, capsys):
        cfg, work, _ref = self._project(
            tmp_path, {"mode": "range", "min_value": -1, "max_value": 0.5}
        )
        code, _err = self._compare(cfg, work, capsys)
        assert code == 1  # the suite's own failures; x reaches 1.001 > 0.5

    BAD = {"mode": "range", "min_value": -1, "max_val": 0.5}

    def _sentence(self, ref) -> str:
        return (
            f"{ref}: comparison.variable_overrides[x]: range · x: "
            'unknown field "max_val" — did you mean "max_value"?'
        )

    @pytest.mark.parametrize(
        "flags", [[], ["--merge"], ["--rerun", "failed"], ["-i", "failed"]]
    )
    def test_a_run_that_scores_refuses_before_it_simulates(
        self, tmp_path, capsys, flags
    ):
        """The baselines a run compares against are there before it starts:
        it simulated every test, and then exited 2 with no verdict."""
        from test_session import _snapshot

        cfg, work, ref = self._project(tmp_path, self.BAD)
        before = _snapshot(work)
        capsys.readouterr()
        code = main(
            ["--config", str(cfg), "run", "--no-browser", "--work-dir", str(work)]
            + flags
        )
        assert code == 2
        assert capsys.readouterr().err.strip().splitlines()[-1] == (
            "Config error: " + self._sentence(ref)
        )
        assert _snapshot(work) == before

    @pytest.mark.parametrize("command", ["compare", "run"])
    def test_a_filter_that_leaves_the_test_out_leaves_its_file_unread(
        self, tmp_path, capsys, command
    ):
        """A command stops for the baselines IT compares against."""
        from test_session import A

        cfg, work, _ref = self._project(tmp_path, self.BAD)
        code = main(
            ["--config", str(cfg), command, "--no-browser", "--work-dir", str(work)]
            + ["--filter", A]
        )
        assert code == 1  # A fails on its own
        assert "Config error" not in capsys.readouterr().err

    def test_a_merged_run_scores_the_tests_its_filter_left_out(self, tmp_path, capsys):
        from test_session import A, _snapshot

        cfg, work, ref = self._project(tmp_path, self.BAD)
        before = _snapshot(work)
        code = main(
            ["--config", str(cfg), "run", "--no-browser", "--work-dir", str(work)]
            + ["--filter", A, "--merge"]
        )
        assert code == 2
        assert self._sentence(ref) in capsys.readouterr().err
        assert _snapshot(work) == before

    @pytest.mark.parametrize("command", ["compare", "run"])
    def test_an_accept_compares_nothing_and_is_not_stopped(
        self, tmp_path, capsys, command
    ):
        cfg, work, _ref = self._project(tmp_path, self.BAD)
        main(
            ["--config", str(cfg), command, "--no-browser", "--work-dir", str(work)]
            + ["--accept"]
        )
        assert "Config error" not in capsys.readouterr().err

    def test_one_comparison_refuses_that_test_alone(self, tmp_path):
        """A caller with one test in hand — the tool, a library caller —
        has that test refused with the sentence; nothing is raised."""
        from dstf.authoring.session import Session
        from dstf.reporting.status import FAIL, PASS, classify, refusal
        from test_session import A, D, G

        pytest.importorskip("nicegui")
        cfg, work, ref = self._project(tmp_path, self.BAD)
        session = Session()
        session.open(cfg, work_dir=work)
        refused = session.comparisons[D]
        assert classify(refused) == FAIL
        assert refusal(refused) == self._sentence(ref)
        assert refused.has_reference is True and refused.variables == []
        # its other tests score
        assert classify(session.comparisons[G]) == PASS
        assert session.comparisons[A].variables[0].nrmse == pytest.approx(
            0.153, abs=1e-3
        )
        assert refusal(session.comparisons[A]) is None


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

    def test_a_range_with_no_bound_is_refused_when_it_is_written(
        self, example, tmp_path, capsys
    ):
        """Every key is one the metric reads, and the leaf can never be
        scored: refused here, where it used to be written and to fail at
        compare time."""
        patch = _patch(
            tmp_path, _add_leaf({"metric": "range", "variable": "unitTests.x[2]"})
        )
        assert self._refused(example, patch, capsys).endswith(
            "range · unitTests.x[2]: needs at least one of min_value / max_value"
        )

    def test_the_misspelled_only_bound(self, example, tmp_path, capsys):
        """The second evidence patch: `max_val` alone — an unknown key AND a
        range left with neither bound. The key is named first."""
        patch = _patch(
            tmp_path,
            _add_leaf(
                {"metric": "range", "variable": "unitTests.x[2]", "max_val": 1.01}
            ),
        )
        assert 'unknown field "max_val"' in self._refused(example, patch, capsys)

    def test_a_scalar_leaf_with_no_quantity(self, example, tmp_path, capsys):
        patch = _patch(
            tmp_path, _add_leaf({"metric": "scalar", "variable": "unitTests.x[1]"})
        )
        assert "scalar · unitTests.x[1]: needs a 'quantity'" in self._refused(
            example, patch, capsys
        )

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

    def test_an_override_that_can_never_score(self, example, tmp_path, capsys):
        """On an entry scored by its flat overrides (no `metrics` tree)."""
        patch = tmp_path / "patch.json"
        op = {
            "op": "add",
            "path": "/comparison/variable_overrides/unitTests.x[1]",
            "value": {"mode": "range"},
        }
        patch.write_text(
            json.dumps({"patches": [{"model": FLAT, "patch": [op]}]}),
            encoding="utf-8",
        )
        assert self._refused(example, patch, capsys) == (
            f"Patch failed: patched entry is invalid: tests[{FLAT}].comparison."
            "variable_overrides[unitTests.x[1]]: range · unitTests.x[1]: "
            "needs at least one of min_value / max_value"
        )

    def test_an_override_under_a_tree_scores_nothing_and_is_not_checked(
        self, example, tmp_path, capsys
    ):
        """MODEL has a `metrics` tree: its flat overrides are read by
        nothing, so one that could not be built fails nothing — the tool
        itself leaves the overrides beside the tree it writes out. Its KEYS
        are still checked (`test_a_misspelled_override_key`)."""
        patch = _patch(
            tmp_path,
            {
                "op": "add",
                "path": "/comparison",
                "value": {"variable_overrides": {"unitTests.x[1]": {"mode": "range"}}},
            },
        )
        assert main(["--config", str(example), "spec-update", str(patch)]) == 0

    def test_the_place_is_written_as_a_parse_error_writes_it(
        self, example, tmp_path, capsys
    ):
        """Dotted — `metrics.children[3]` — never the op's JSON pointer."""
        patch = _patch(
            tmp_path, _add_leaf({"metric": "range", "variable": "unitTests.x[2]"})
        )
        assert self._refused(example, patch, capsys) == (
            f"Patch failed: patched entry is invalid: tests[{MODEL}].metrics."
            "children[3]: range · unitTests.x[2]: needs at least one of "
            "min_value / max_value"
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


BARE_RANGE = {"metric": "range", "variable": "unitTests.x[2]"}
BARE_SCALAR = {"metric": "scalar", "variable": "unitTests.x[1]"}


class TestOnlyTheWriteThatMakesAnUnscorableCriterionIsRefused:
    """The delta rule (`spec_update.refuse_newly_unscorable`): a criterion
    that is unscorable after the patch is refused only when it was not
    already there, with the same content, before it. Refusing EVERY one of
    the patched entry refused each edit of an entry that already held one —
    the edit that filled it too — and two of them blocked each other."""

    def _held(self, example, model, change) -> Path:
        """The example with ``change`` made to ``model``'s entry on disk —
        a spec written by hand, or by the tool before the check."""
        spec = example.parent / "test_spec.json"
        data = json.loads(spec.read_text(encoding="utf-8"))
        (entry,) = [e for e in data["tests"] if e["model"] == model]
        change(entry)
        spec.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return spec

    def _update(self, example, tmp_path, model, *ops) -> int:
        patch = tmp_path / "patch.json"
        patch.write_text(
            json.dumps({"patches": [{"model": model, "patch": list(ops)}]}),
            encoding="utf-8",
        )
        return main(["--config", str(example), "spec-update", str(patch)])

    def _entry(self, spec, model) -> dict:
        data = json.loads(spec.read_text(encoding="utf-8"))
        (entry,) = [e for e in data["tests"] if e["model"] == model]
        return entry

    def _two(self, entry):
        entry["metrics"]["children"] += [dict(BARE_RANGE), dict(BARE_SCALAR)]

    def test_an_untouched_one_does_not_block_another_edit(self, example, tmp_path):
        spec = self._held(example, MODEL, self._two)
        op = {"op": "add", "path": "/metrics/children/0/tolerance", "value": 0.5}
        assert self._update(example, tmp_path, MODEL, op) == 0
        assert self._entry(spec, MODEL)["metrics"]["children"][0]["tolerance"] == 0.5

    def test_nor_a_plots_edit(self, example, tmp_path):
        self._held(example, MODEL, self._two)
        op = {"op": "add", "path": "/plots", "value": [{"var": "unitTests.x[1]"}]}
        assert self._update(example, tmp_path, MODEL, op) == 0

    def test_one_of_two_may_be_filled(self, example, tmp_path):
        spec = self._held(example, MODEL, self._two)
        op = {"op": "add", "path": "/metrics/children/3/min_value", "value": 0.0}
        assert self._update(example, tmp_path, MODEL, op) == 0
        assert self._entry(spec, MODEL)["metrics"]["children"][3]["min_value"] == 0.0

    def test_one_of_two_may_be_removed_and_the_other_only_moves(
        self, example, tmp_path
    ):
        """The scalar leaf is at children[3] afterwards, where the range
        was: a rule that looked a place up would call it new."""
        spec = self._held(example, MODEL, self._two)
        op = {"op": "remove", "path": "/metrics/children/3"}
        assert self._update(example, tmp_path, MODEL, op) == 0
        assert self._entry(spec, MODEL)["metrics"]["children"][3] == BARE_SCALAR

    def test_an_edit_that_leaves_one_unscorable_is_refused(
        self, example, tmp_path, capsys
    ):
        """It touched the criterion and did not fill it: its content is
        not what was there."""
        spec = self._held(example, MODEL, self._two)
        before = spec.read_bytes()
        op = {"op": "add", "path": "/metrics/children/3/tolerance", "value": 0.5}
        assert self._update(example, tmp_path, MODEL, op) == 1
        assert "children[3]: range · unitTests.x[2]: needs at least one" in (
            capsys.readouterr().out
        )
        assert spec.read_bytes() == before

    def test_a_second_equal_one_is_a_new_one(self, example, tmp_path, capsys):
        """Counted: one bare range before, two after."""
        spec = self._held(example, MODEL, self._two)
        before = spec.read_bytes()
        assert self._update(example, tmp_path, MODEL, _add_leaf(BARE_RANGE)) == 1
        assert "children[5]: range · unitTests.x[2]" in capsys.readouterr().out
        assert spec.read_bytes() == before

    def _override(self, entry):
        entry["comparison"]["variable_overrides"]["unitTests.x[1]"] = {"mode": "range"}

    def test_a_held_override_takes_its_fix_and_any_other_edit(self, example, tmp_path):
        spec = self._held(example, FLAT, self._override)
        op = {"op": "add", "path": "/comparison/tolerance", "value": 0.05}
        assert self._update(example, tmp_path, FLAT, op) == 0
        fix = {
            "op": "add",
            "path": "/comparison/variable_overrides/unitTests.x[1]/min_value",
            "value": 0.0,
        }
        assert self._update(example, tmp_path, FLAT, fix) == 0
        overrides = self._entry(spec, FLAT)["comparison"]["variable_overrides"]
        assert overrides["unitTests.x[1]"] == {"mode": "range", "min_value": 0.0}

    def test_writing_the_entrys_tree_out_makes_no_new_one(self, example, tmp_path):
        """A per-leaf op on a flat entry writes its implicit tree first: the
        held override is then a bare leaf of that tree — the same
        criterion, not a new one."""

        def held(entry):
            entry["variables"] = ["unitTests.x[1]", "unitTests.x[2]"]
            self._override(entry)

        spec = self._held(example, FLAT, held)
        op = {"op": "add", "path": "/metrics/children/1/tolerance", "value": 0.5}
        assert self._update(example, tmp_path, FLAT, op) == 0
        first = self._entry(spec, FLAT)["metrics"]["children"][0]
        assert first == {"metric": "range", "variable": "unitTests.x[1]"}

    def test_removing_the_tree_over_a_bare_override_makes_it_score(
        self, example, tmp_path, capsys
    ):
        """Under the tree the override scored nothing and was not looked
        at; with the tree gone it is a criterion, and a new one."""

        def held(entry):
            entry["comparison"] = {
                "variable_overrides": {"unitTests.x[1]": {"mode": "range"}}
            }

        spec = self._held(example, MODEL, held)
        before = spec.read_bytes()
        op = {"op": "remove", "path": "/metrics"}
        assert self._update(example, tmp_path, MODEL, op) == 1
        assert "comparison.variable_overrides[unitTests.x[1]]" in (
            capsys.readouterr().out
        )
        assert spec.read_bytes() == before


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

    def test_the_root_is_closed_to_the_parsers_keys(self, schema):
        from dstf.discovery import spec_shape

        jsonschema = pytest.importorskip("jsonschema")
        assert schema["additionalProperties"] is False
        assert set(schema["properties"]) == set(spec_shape.ROOT) == {"$schema", "tests"}
        jsonschema.validate({"$schema": "./s.json", "tests": []}, schema)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"Tests": [], "tests": []}, schema)

    @pytest.mark.parametrize(
        "quantity", [{"reduse": "max"}, {"reduce": "max", "over": "x"}]
    )
    def test_a_scalars_quantity_object_is_closed(self, schema, quantity):
        jsonschema = pytest.importorskip("jsonschema")
        leaf = {"metric": "scalar", "variable": "x", "quantity": quantity}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"tests": [{"model": "L.M", "metrics": leaf}]}, schema)
        override = {"mode": "scalar", "quantity": quantity}
        entry = {"model": "L.M", "comparison": {"variable_overrides": {"x": override}}}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"tests": [entry]}, schema)
        for taken in ({"reduce": "max"}, "CPUtime.final"):
            leaf = {"metric": "scalar", "variable": "x", "quantity": taken}
            jsonschema.validate({"tests": [{"model": "L.M", "metrics": leaf}]}, schema)

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


# ---------------------------------------------------------------------------
# the authoring tool and a criterion that could never be scored: staged,
# said by its row, refused by Save — and only when an edit made it
# ---------------------------------------------------------------------------

BARE_TREE = {
    "combinator": "and",
    "children": [
        {"metric": "nrmse", "variable": "x"},
        {"metric": "range", "variable": "pipe.T[1]"},
    ],
}


class TestTheToolStagesAnUnscorableCriterionAndSaveRefusesIt:
    @pytest.fixture
    def project(self, tmp_path):
        pytest.importorskip("nicegui")
        from family_project import family_project

        return family_project(tmp_path, 3, tracked=(1, 2), fail=())

    def _open(self, project, change=None):
        """A Session over ``project``, ``change`` first made to A's entry on
        disk."""
        from dstf.authoring.session import Session
        from test_session import A

        cfg, work = project
        if change is not None:
            spec = cfg.parent / "test_spec.json"
            data = json.loads(spec.read_text(encoding="utf-8"))
            (entry,) = [e for e in data["tests"] if e["model"] == A]
            change(entry)
            spec.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        session = Session()
        session.open(cfg, work_dir=work)
        return session

    def _saved(self, session) -> dict:
        from test_session import A

        data = json.loads(session.spec.path.read_text(encoding="utf-8"))
        (entry,) = [e for e in data["tests"] if e["model"] == A]
        return entry

    def test_it_is_staged_and_its_row_says_why_it_is_not_scored(self, project):
        from dstf.authoring.spec_document import PatchOp
        from test_session import A

        session = self._open(project)
        session.stage([PatchOp(A, "add", "/metrics", BARE_TREE)])
        assert len(session.spec.pending) == 1
        comparison = session.comparisons[A]
        assert comparison.passed is False
        (bare,) = [v for v in comparison.variables if v.name == "pipe.T[1]"]
        assert bare.diagnostics["error"] == (
            "metric 'range' misconfigured: needs at least one of min_value / max_value"
        )

    def test_save_refuses_it_in_one_sentence_and_keeps_the_edits(self, project):
        from dstf.authoring.session import UnscorableSaveError
        from dstf.authoring.spec_document import PatchOp
        from test_session import A

        session = self._open(project)
        before = session.spec.path.read_bytes()
        session.stage([PatchOp(A, "add", "/metrics", BARE_TREE)])
        with pytest.raises(UnscorableSaveError) as refused:
            session.save()
        assert str(refused.value) == (
            "range · pipe.T[1]: needs at least one of min_value / max_value"
            " — fill it or remove it before saving"
        )
        assert session.spec.path.read_bytes() == before
        assert len(session.spec.pending) == 1
        # filled, it saves
        session.stage([PatchOp(A, "add", "/metrics/children/1/min_value", 0.0)])
        session.save()
        assert self._saved(session)["metrics"]["children"][1]["min_value"] == 0.0

    @pytest.mark.parametrize("metric", ["range", "scalar"])
    def test_a_criterion_is_added_on_a_variable_with_no_data_to_seed_it(
        self, project, metric
    ):
        """The add form's leaf has nothing to be seeded from: it is staged
        bare, for its editor to fill."""
        from dstf.authoring.builtin.edit_tree import EditTree
        from dstf.authoring.session import Selection, SessionView
        from test_session import A

        session = self._open(project)
        view = SessionView(session, Selection(tests=frozenset({A})))
        session.apply(
            EditTree().act(
                view,
                action="add",
                model_id=A,
                path="/metrics",
                variable="nosuch.y",
                metric=metric,
            )
        )
        added = session.tree(A)["children"][-1]
        assert (added["metric"], added["variable"]) == (metric, "nosuch.y")

    def _bare_override(self, entry):
        entry["comparison"] = {"variable_overrides": {"x": {"mode": "range"}}}

    def test_an_entry_that_holds_one_takes_the_edit_that_fills_it(self, project):
        from dstf.authoring.builtin.edit_criteria import EditCriteria
        from dstf.authoring.session import Selection, SessionView
        from test_session import A

        session = self._open(project, self._bare_override)
        view = SessionView(session, Selection(tests=frozenset({A}), variable="x"))
        session.apply(
            EditCriteria().act(
                view,
                model_id=A,
                path="/metrics/children/0",
                metric="range",
                variable="x",
                params={"min_value": -5.0},
                against="primary",
                window={},
            )
        )
        session.save()
        assert self._saved(session)["metrics"]["children"][0] == {
            "metric": "range",
            "variable": "x",
            "min_value": -5.0,
        }

    def test_and_the_edit_that_removes_it_and_one_that_adds_beside_it(self, project):
        from dstf.authoring.builtin.edit_tree import EditTree
        from dstf.authoring.session import Selection, SessionView
        from test_session import A

        session = self._open(project, self._bare_override)
        view = SessionView(session, Selection(tests=frozenset({A})))
        session.apply(
            EditTree().act(
                view,
                action="add",
                model_id=A,
                path="/metrics",
                variable="pipe.T[2]",
                metric="nrmse",
            )
        )
        session.save()  # the bare one is the one that was there
        assert self._saved(session)["metrics"]["children"][0] == {
            "metric": "range",
            "variable": "x",
        }
        session.apply(
            EditTree().act(
                view, action="remove", model_id=A, path="/metrics/children/0"
            )
        )
        session.save()
        assert all(
            leaf["metric"] != "range"
            for leaf in self._saved(session)["metrics"]["children"]
        )

    def test_a_spec_the_tool_saved_with_an_override_left_under_its_tree(self, project):
        """What the tool wrote before the check: the entry's tree, filled,
        and the flat override it came from still beside it, scoring
        nothing. Every edit of that entry was refused."""
        from dstf.authoring.spec_document import PatchOp
        from test_session import A

        def left(entry):
            self._bare_override(entry)
            entry["metrics"] = {
                "combinator": "and",
                "children": [{"metric": "range", "variable": "x", "min_value": -5.0}],
            }

        session = self._open(project, left)
        session.stage([PatchOp(A, "add", "/metrics/children/0/max_value", 9.0)])
        session.stage([PatchOp(A, "add", "/plots", [{"var": "x"}])])
        session.stage([PatchOp(A, "remove", "/comparison/variable_overrides/x")])
        session.save()
        assert self._saved(session)["metrics"]["children"][0]["max_value"] == 9.0
