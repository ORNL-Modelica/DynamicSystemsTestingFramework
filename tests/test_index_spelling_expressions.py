"""An expression stays as the model wrote it — it is a label — but where
one is used to FIND a name it is read in the one spelling (#28): a
UnitTests expression ``pipe.T[1, 1]`` meets a companion's or an
override's ``pipe.T[1,1]``."""

from __future__ import annotations

import logging

import numpy as np
import pytest

from conftest import make_test_model
from dstf.backends.types import TestResult, VariableResult
from dstf.comparison.comparator import compare_test
from dstf.discovery.mo_parser import normalize_expression
from dstf.reporting.overlay_loader import (
    Overlay,
    OverlayVariable,
    attach_overlays_to_trajectories,
)
from dstf.storage.baseline_codec import decode_reference


def test_a_model_s_expression_keeps_its_spaces():
    assert normalize_expression("pipe.T[1,  1]") == "pipe.T[1, 1]"


def _overlay(**variables: OverlayVariable) -> Overlay:
    return Overlay(name="rig", role="companion", status="loaded", variables=variables)


def test_a_companion_named_like_the_expression_draws_on_it():
    trajectories = [{"name": "unitTests.x[1]", "expression": "pipe.T[1, 1]"}]
    overlays = [
        _overlay(**{"pipe.T[1,1]": OverlayVariable(time=[0, 1], values=[4, 5])})
    ]
    attach_overlays_to_trajectories(trajectories, overlays)
    assert [o["values"] for o in trajectories[0]["overlays"]] == [[4, 5]]


@pytest.mark.parametrize(
    "expression",
    # As `normalize_expression` keeps it (the common case), and the other way.
    ["pipe.T[1, 1]", "pipe.T[1,1]"],
)
def test_a_companion_whose_expression_names_the_variable_draws_on_it(expression):
    """Both sides' expressions are compared in the one spelling: the
    trajectory's and the companion variable's."""
    trajectories = [{"name": "unitTests.x[1]", "expression": "pipe.T[1, 1]"}]
    overlays = [
        _overlay(
            **{
                "ut.x[3]": OverlayVariable(
                    time=[0, 1], values=[6, 7], expression=expression
                )
            }
        )
    ]
    attach_overlays_to_trajectories(trajectories, overlays)
    assert [o["values"] for o in trajectories[0]["overlays"]] == [[6, 7]]


def test_an_override_keyed_by_an_expression_with_a_space_names_its_variable(
    caplog,
):
    """The spec's key is ``pipe.T[1,1]`` (the decoder's spelling); the
    model wrote ``pipe.T[1, 1]``. The warning still says which variable
    the key meant."""
    t = np.linspace(0.0, 1.0, 5)
    test = make_test_model(
        "Lib.T",
        source="unit_tests",
        n_vars=1,
        variable_overrides={"pipe.T[1,1]": {"tolerance": 0.1}},
    )
    result = TestResult(
        "Lib.T",
        True,
        variables=[
            VariableResult(1, t, t, name="unitTests.x[1]", expression="pipe.T[1, 1]")
        ],
    )
    reference = decode_reference(
        {
            "time": list(t),
            "variables": [{"name": "unitTests.x[1]", "values": list(t)}],
        }
    )
    with caplog.at_level(logging.WARNING):
        compare_test(test, result, reference)
    (line,) = [
        r.getMessage() for r in caplog.records if "variable_overrides" in r.getMessage()
    ]
    assert line == (
        "Lib.T: variable_overrides key(s) name no variable of this result and "
        "are ignored: 'pipe.T[1,1]' in test_spec.json (the expression of "
        "'unitTests.x[1]') — a key that names an expression must be the "
        "result-file name of the variable it means, named beside it"
    )
