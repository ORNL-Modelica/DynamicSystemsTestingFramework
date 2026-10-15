"""Tests for the pluggable recognizer registry (PTA.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

# mo_parser registers the bundled recognizer when it is imported. Import it
# here, before the fixture's first snapshot: imported later (by the first
# test), the snapshot would be the empty registry, the restore would drop the
# bundled recognizer, and Python never imports the module again — the file
# then failed run alone and passed only after another file had imported it.
from dstf.discovery import mo_parser  # noqa: F401
from dstf.discovery import recognizer as recognizer_module
from dstf.discovery.recognizer import (
    Recognizer,
    RecognizerResult,
    get_recognizers,
)
from dstf.discovery.unit_tests import unit_test_variables

PROJECT_ROOT = Path(__file__).parent.parent
SAMPLE_DIR = PROJECT_ROOT / "examples" / "modelica" / "ModelicaTestingLib"


@pytest.fixture(autouse=True)
def reset_registry_after_test():
    """Snapshot the registry; restore after each test so user-recognizer
    tests don't leak into the bundled-recognizer behavior tests."""
    saved = list(recognizer_module._REGISTRY)
    yield
    recognizer_module._REGISTRY[:] = saved


class TestBundledRecognizer:
    """The bundled Modelica recognizer is auto-registered on mo_parser import."""

    def test_bundled_registers_on_mo_parser_import(self):
        # Import triggers registration

        recs = get_recognizers("modelica")
        names = [r.name for r in recs]
        assert "modelica:bundled-unit-tests" in names

    def test_bundled_filters_by_source_type(self):

        # Bundled recognizer applies only to "modelica"
        recs = get_recognizers("modelica")
        assert any(r.name == "modelica:bundled-unit-tests" for r in recs)

        # Other source types see nothing from this recognizer
        fmu_recs = get_recognizers("fmu")
        assert not any(r.name == "modelica:bundled-unit-tests" for r in fmu_recs)

    def test_bundled_extracts_simple_test(self):

        recs = [
            r
            for r in get_recognizers("modelica")
            if r.name == "modelica:bundled-unit-tests"
        ]
        assert len(recs) == 1
        rec = recs[0]

        result = rec.recognize(SAMPLE_DIR / "Examples" / "SimpleTest.mo")
        assert result is not None
        assert result.model_id == "ModelicaTestingLib.Examples.SimpleTest"
        assert result.n_vars == 2
        assert result.x_expressions == ["x", "y"]
        assert result.stop_time == 10.0
        assert result.tolerance == 1e-6
        assert result.method == "Dassl"

    def test_bundled_threads_the_instance_name(self, tmp_path):
        """The UnitTests component's instance name reaches the TestModel —
        its variables are ``<instance>.x[i]`` in the result file."""
        from dstf.discovery.test_registry import (
            _build_test_model_from_recognizer_results,
        )

        mo = tmp_path / "M.mo"
        mo.write_text(
            "within L;\nmodel M\n  Real x;\n  UnitTests ut(n=1, x={x});\nend M;\n",
            encoding="utf-8",
        )
        rec = [
            r
            for r in get_recognizers("modelica")
            if r.name == "modelica:bundled-unit-tests"
        ][0]
        result = rec.recognize(mo)
        assert result.unit_tests_instance == "ut"
        merged = _build_test_model_from_recognizer_results("L.M", [result])
        assert merged.unit_tests_instance == "ut"

    def test_bundled_returns_none_for_non_test_file(self):

        recs = [
            r
            for r in get_recognizers("modelica")
            if r.name == "modelica:bundled-unit-tests"
        ]
        rec = recs[0]
        assert rec.recognize(SAMPLE_DIR / "Examples" / "NoUnitTest.mo") is None


class _StubRecognizer(Recognizer):
    """In-test recognizer that emits a fixed result for one model."""

    def __init__(self, name: str, model_id: str, **fields):
        self.name = name
        self.applies_to = frozenset({"modelica"})
        self._model_id = model_id
        self._fields = fields

    def recognize(self, source_file: Path) -> RecognizerResult | None:
        # Match only files whose stem ends with the model's short name
        short = self._model_id.rsplit(".", 1)[-1]
        if source_file.stem != short:
            return None
        return RecognizerResult(
            model_id=self._model_id,
            source_file=source_file,
            **self._fields,
        )


class TestRegistryMerge:
    """Multiple recognizers emitting for the same model_id merge per-field;
    later registrations override earlier ones."""

    def test_user_recognizer_overrides_bundled_field(self, tmp_path):
        # Both recognizers see the same .mo file; user one sets stop_time=99,
        # bundled sets it from the experiment annotation. User wins.
        from dstf.discovery.test_registry import (
            _build_test_model_from_recognizer_results,
        )

        bundled = [
            r
            for r in get_recognizers("modelica")
            if r.name == "modelica:bundled-unit-tests"
        ][0]
        bundled_result = bundled.recognize(SAMPLE_DIR / "Examples" / "SimpleTest.mo")
        assert bundled_result.stop_time == 10.0

        user_result = RecognizerResult(
            model_id=bundled_result.model_id,
            stop_time=99.0,
        )

        merged = _build_test_model_from_recognizer_results(
            bundled_result.model_id,
            [bundled_result, user_result],
        )
        assert merged.stop_time == 99.0
        # Bundled-only fields survive
        assert merged.n_vars == 2
        assert merged.x_expressions == ["x", "y"]

    def test_recognizer_only_sets_fields_it_knows(self):
        """A recognizer that only sets stop_time leaves other fields untouched."""
        from dstf.discovery.test_registry import (
            _build_test_model_from_recognizer_results,
        )

        partial = RecognizerResult(model_id="My.Model", stop_time=5.0)
        merged = _build_test_model_from_recognizer_results("My.Model", [partial])

        assert merged.stop_time == 5.0
        # no recognizer declared a UnitTests variable, so the test has none:
        # a default of 1 made every such test look for `unitTests.x[1]`
        assert merged.n_vars == 0
        assert unit_test_variables(merged) == []
        assert merged.x_expressions == []
        # and the UnitTests component's conventional instance name stands
        assert merged.unit_tests_instance == "unitTests"

    def test_expressions_without_a_count_are_as_many_variables(self):
        """A recognizer that gives the expressions and no ``n_vars`` declared
        that many variables — the count is theirs, not a default."""
        from dstf.discovery.test_registry import (
            _build_test_model_from_recognizer_results,
        )

        result = RecognizerResult(model_id="L.M", x_expressions=["a", "b", "c"])
        merged = _build_test_model_from_recognizer_results("L.M", [result])
        assert merged.n_vars == 3
        assert [v.name for v in unit_test_variables(merged)] == [
            "unitTests.x[1]",
            "unitTests.x[2]",
            "unitTests.x[3]",
        ]

    def test_a_declared_count_stands_over_the_expressions(self):
        """``n_vars`` from any recognizer is the count, whatever the number
        of expressions another one gave (one expression may cover an array)."""
        from dstf.discovery.test_registry import (
            _build_test_model_from_recognizer_results,
        )

        merged = _build_test_model_from_recognizer_results(
            "L.M",
            [
                RecognizerResult(model_id="L.M", n_vars=4),
                RecognizerResult(model_id="L.M", x_expressions=["y"]),
            ],
        )
        assert merged.n_vars == 4

    def test_a_later_recognizer_without_an_instance_keeps_the_earlier_one(self):
        """A field not contributed is not a field contributed empty: a second recognizer
        that only adjusts the stop time must not blank the instance name the
        first one read off the component."""
        from dstf.discovery.test_registry import (
            _build_test_model_from_recognizer_results,
        )

        first = RecognizerResult(
            model_id="L.M", n_vars=1, unit_tests_instance="ut", x_expressions=["a"]
        )
        later = RecognizerResult(model_id="L.M", stop_time=5.0)
        merged = _build_test_model_from_recognizer_results("L.M", [first, later])
        assert merged.unit_tests_instance == "ut"
        assert merged.stop_time == 5.0

    def test_a_both_test_keeps_the_scanned_instance(self):
        """A spec entry for a scanned test names no component, so the merge
        must leave the scanned instance alone — the component's variables are
        still ``ut.x[i]`` in the result file."""
        from dstf.discovery.test_registry import (
            TestModel,
            _build_test_model_from_recognizer_results,
            merge_tests,
        )

        scanned = _build_test_model_from_recognizer_results(
            "L.M",
            [
                RecognizerResult(
                    model_id="L.M",
                    n_vars=1,
                    unit_tests_instance="ut",
                    x_expressions=["a"],
                )
            ],
        )
        spec = TestModel(
            model_id="L.M",
            source_file=None,
            source_package="L",
            short_name="M",
            n_vars=0,
            variable_patterns=["q"],
            source="spec",
        )
        (merged,) = merge_tests({"L.M": scanned}, {"L.M": spec})
        assert merged.source == "both"
        assert merged.unit_tests_instance == "ut"
