"""Tests for the pluggable recognizer registry (PTA.1)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest

from modelica_testing.discovery import recognizer as recognizer_module
from modelica_testing.discovery.recognizer import (
    Recognizer,
    RecognizerResult,
    get_recognizers,
    register,
)


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
        from modelica_testing.discovery import mo_parser  # noqa: F401

        recs = get_recognizers("modelica")
        names = [r.name for r in recs]
        assert "modelica:bundled-unit-tests" in names

    def test_bundled_filters_by_source_type(self):
        from modelica_testing.discovery import mo_parser  # noqa: F401

        # Bundled recognizer applies only to "modelica"
        recs = get_recognizers("modelica")
        assert any(r.name == "modelica:bundled-unit-tests" for r in recs)

        # Other source types see nothing from this recognizer
        fmu_recs = get_recognizers("fmu")
        assert not any(r.name == "modelica:bundled-unit-tests" for r in fmu_recs)

    def test_bundled_extracts_simple_test(self):
        from modelica_testing.discovery import mo_parser  # noqa: F401

        recs = [r for r in get_recognizers("modelica")
                if r.name == "modelica:bundled-unit-tests"]
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

    def test_bundled_returns_none_for_non_test_file(self):
        from modelica_testing.discovery import mo_parser  # noqa: F401

        recs = [r for r in get_recognizers("modelica")
                if r.name == "modelica:bundled-unit-tests"]
        rec = recs[0]
        assert rec.recognize(SAMPLE_DIR / "Examples" / "NoUnitTest.mo") is None


class _StubRecognizer(Recognizer):
    """In-test recognizer that emits a fixed result for one model."""

    def __init__(self, name: str, model_id: str, **fields):
        self.name = name
        self.applies_to = frozenset({"modelica"})
        self._model_id = model_id
        self._fields = fields

    def recognize(self, source_file: Path) -> Optional[RecognizerResult]:
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
        from modelica_testing.discovery import mo_parser  # noqa: F401
        from modelica_testing.discovery.test_registry import (
            _build_test_model_from_recognizer_results,
        )

        bundled = [r for r in get_recognizers("modelica")
                   if r.name == "modelica:bundled-unit-tests"][0]
        bundled_result = bundled.recognize(SAMPLE_DIR / "Examples" / "SimpleTest.mo")
        assert bundled_result.stop_time == 10.0

        user_result = RecognizerResult(
            model_id=bundled_result.model_id,
            stop_time=99.0,
        )

        merged = _build_test_model_from_recognizer_results(
            bundled_result.model_id, [bundled_result, user_result],
        )
        assert merged.stop_time == 99.0
        # Bundled-only fields survive
        assert merged.n_vars == 2
        assert merged.x_expressions == ["x", "y"]

    def test_recognizer_only_sets_fields_it_knows(self):
        """A recognizer that only sets stop_time leaves other fields untouched."""
        from modelica_testing.discovery.test_registry import (
            _build_test_model_from_recognizer_results,
        )

        partial = RecognizerResult(model_id="My.Model", stop_time=5.0)
        merged = _build_test_model_from_recognizer_results("My.Model", [partial])

        assert merged.stop_time == 5.0
        # n_vars defaults to 1 when no recognizer sets it
        assert merged.n_vars == 1
        assert merged.x_expressions == []
