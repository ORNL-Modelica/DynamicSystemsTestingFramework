"""The one snap rule (D149): a value the POINTER placed — dragged or clicked
— is written at a readable step of the axis it was placed on."""

from __future__ import annotations

import math

import pytest

from dstf.comparison.snap import snap_step, snapped


@pytest.mark.parametrize("decade", range(-6, 7))
def test_the_step_is_the_power_of_ten_nearest_a_thousandth_of_the_span(decade):
    """Over twelve decades of span: 1, 2 and 3 of a decade round down to its
    thousandth; 3.2 and up are nearer the next power of ten (the nearest in
    the exponent: 10 ** round(log10(span / 1000)))."""
    unit = 10.0**decade
    for factor in (1.0, 2.0, 3.0, 3.1):
        assert snap_step(factor * unit) == pytest.approx(unit / 1000, rel=1e-12)
    for factor in (3.2, 5.0, 9.9):
        assert snap_step(factor * unit) == pytest.approx(unit / 100, rel=1e-12)


@pytest.mark.parametrize("span", [None, 0, 0.0, -1.0, math.inf, math.nan, True, "1"])
def test_no_span_is_no_step_and_nothing_is_snapped(span):
    assert snap_step(span) is None
    assert snapped(0.123456789, span) == 0.123456789
    assert snapped(0.123456789, span, fraction=True) == 0.123456789


@pytest.mark.parametrize(
    "value, span, want",
    [
        (0.373778382112, 1.0, 0.374),
        (0.373778382112, 0.1, 0.3738),
        (0.373778382112, 10.0, 0.37),
        (1234.5678, 2e5, 1200.0),  # a step of 100: a multiple of it
        (1234.5678, 2e4, 1230.0),
        (-0.373778382112, 1.0, -0.374),
        (-1234.5678, 2e4, -1230.0),
        (0.30000000000000004, 1.0, 0.3),  # exactly the float a person types
        (2.0000000001e-4, 1e-3, 2e-4),
    ],
)
def test_a_coordinate_is_rounded_to_a_multiple_of_the_step(value, span, want):
    assert snapped(value, span) == want
    assert repr(snapped(value, span)) == repr(want)


@pytest.mark.parametrize(
    "value, span, want",
    [
        # Exactly half way between two multiples, and exactly representable:
        # to the EVEN multiple, as Python rounds.
        (0.5, 1000.0, 0.0),
        (1.5, 1000.0, 2.0),
        (2.5, 1000.0, 2.0),
        (-2.5, 1000.0, -2.0),
        (0.125, 10.0, 0.12),
        (0.375, 10.0, 0.38),
        (-0.125, 10.0, -0.12),
        # 0.0125 is NOT half way: the float is a hair above it.
        (0.0125, 1.0, 0.013),
    ],
)
def test_an_exact_half_goes_to_the_even_multiple(value, span, want):
    assert snapped(value, span) == want


def test_a_value_that_snaps_to_zero_is_a_plain_zero():
    """Never −0.0: a side dragged a hair across its target and snapped back
    onto it is written `0.0`, as a typed zero is."""
    for value in (-0.0004, -0.0, 0.0004):
        got = snapped(value, 1.0)
        assert got == 0.0 and math.copysign(1.0, got) == 1.0
    got = snapped(-0.0, 1.0, fraction=True)
    assert got == 0.0 and math.copysign(1.0, got) == 1.0


@pytest.mark.parametrize(
    "value, want",
    [
        (0.186889191056, 0.187),
        (0.0123456, 0.0123),
        (1.23456, 1.23),
        (123456.0, 123000.0),
        (-0.0123456, -0.0123),
        (0.1, 0.1),
        (9.996e-5, 0.0001),
    ],
)
def test_a_relative_side_is_three_significant_digits_whatever_the_span(value, want):
    for span in (1e-6, 1.0, 1e6):
        assert snapped(value, span, fraction=True) == want


def test_snapping_is_idempotent_and_stays_within_half_a_step():
    for span in (0.02, 0.7, 1.0, 45.0, 3600.0):
        step = snap_step(span)
        for k in range(200):
            value = -span + k * span / 97.3
            got = snapped(value, span)
            assert abs(got - value) <= step / 2 * (1 + 1e-9)
            assert snapped(got, span) == got
