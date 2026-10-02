"""Sides: a tolerance is a number or a lower/upper pair — one reader, one writer."""

import math

import pytest

from dstf.comparison.sides import Sides, as_written, parse_sides, sides_text


class TestParse:
    @pytest.mark.parametrize("v", [0, 1, 0.5, -2.0, 1e-9])
    def test_a_number_is_both_sides(self, v):
        s = parse_sides(v, "tolerance")
        assert s == Sides(float(v), float(v))
        assert s.equal
        assert isinstance(s.lower, float) and isinstance(s.upper, float)

    def test_a_pair(self):
        s = parse_sides({"lower": 0.1, "upper": 2}, "tolerance")
        assert s == Sides(0.1, 2.0)
        assert not s.equal
        assert isinstance(s.upper, float)

    def test_an_equal_pair_is_equal(self):
        assert parse_sides({"lower": 3, "upper": 3}, "t").equal

    def test_sides_is_itself(self):
        s = Sides(1.0, 2.0)
        assert parse_sides(s, "t") is s

    @pytest.mark.parametrize(
        "bad",
        [
            {"lower": 1},
            {"upper": 1},
            {},
            {"lower": 1, "upper": 2, "extra": 3},
            {"lower": "1", "upper": 2},
            {"lower": True, "upper": 2},
            {"lower": 1, "upper": False},
            {"lower": 1, "upper": None},
            {"lower": 1, "upper": float("nan")},
            {"lower": float("inf"), "upper": 1},
            "abc",
            "0.1",
            None,
            True,
            False,
            float("nan"),
            float("inf"),
            -float("inf"),
            [1, 2],
        ],
        ids=repr,
    )
    def test_a_refusal_names_what_and_the_value(self, bad):
        with pytest.raises(ValueError) as exc:
            parse_sides(bad, "points[1].tolerance")
        assert str(exc.value) == (
            'points[1].tolerance must be a number or {"lower": …, "upper": …}, '
            f"got {bad!r}"
        )

    def test_no_sign_check(self):
        assert parse_sides(-1, "t") == Sides(-1.0, -1.0)
        assert parse_sides({"lower": -1, "upper": 2}, "t") == Sides(-1.0, 2.0)

    def test_sides_does_not_compare_equal_to_a_number(self):
        assert Sides(0.5, 0.5) != 0.5


class TestWritten:
    def test_equal_sides_are_the_number(self):
        w = as_written(Sides(0.5, 0.5))
        assert w == 0.5 and isinstance(w, float)

    def test_unequal_sides_are_the_pair(self):
        assert as_written(Sides(0.1, 0.2)) == {"lower": 0.1, "upper": 0.2}

    @pytest.mark.parametrize("s", [Sides(0.5, 0.5), Sides(0.1, 0.2), Sides(0.0, 0.0)])
    def test_round_trip(self, s):
        assert parse_sides(as_written(s), "t") == s


class TestText:
    def test_equal(self):
        assert sides_text(Sides(0.01, 0.01)) == "1.000e-02"
        assert sides_text(Sides(0.01, 0.01), "g") == "0.01"

    def test_unequal(self):
        assert sides_text(Sides(0.01, 0.02)) == "−1.000e-02 / +2.000e-02"
        assert sides_text(Sides(0.5, 1.0), "g") == "−0.5 / +1"

    def test_equal_text_is_the_plain_format(self):
        v = math.pi
        assert sides_text(Sides(v, v)) == f"{v:.3e}"
