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

    def test_any_real_number_that_is_no_bool(self):
        """As ``float()`` took them: a numpy scalar is a number."""
        import numpy as np

        assert parse_sides(np.float32(0.5), "t") == Sides(0.5, 0.5)
        assert parse_sides(np.int64(2), "t") == Sides(2.0, 2.0)
        s = parse_sides({"lower": np.float64(0.1), "upper": np.int32(2)}, "t")
        assert s == Sides(0.1, 2.0)
        assert type(s.lower) is float and type(s.upper) is float
        for bad in (np.bool_(True), np.float32("nan"), np.float64("inf")):
            with pytest.raises(ValueError, match="t must be a number or "):
                parse_sides(bad, "t")

    @pytest.mark.parametrize(
        "bad", [10**400, {"lower": 1, "upper": -(10**400)}], ids=["number", "side"]
    )
    def test_an_int_too_large_for_a_float_is_the_same_refusal(self, bad):
        with pytest.raises(ValueError) as exc:
            parse_sides(bad, "tolerance")
        assert str(exc.value) == (
            f'tolerance must be a number or {{"lower": …, "upper": …}}, got {bad!r}'
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

    def test_a_negative_side_is_the_signed_number(self):
        """``−lower / +upper`` are the band's two edges, signed: a negative
        lower puts its edge ABOVE the target, where "−-1.000e-01" said
        nothing a person can read."""
        assert sides_text(Sides(-0.1, 0.5)) == "+1.000e-01 / +5.000e-01"
        assert sides_text(Sides(0.1, -0.5)) == "−1.000e-01 / −5.000e-01"
        assert sides_text(Sides(-0.5, 1.0), "g") == "+0.5 / +1"
        # The exponent's sign is not the number's.
        assert sides_text(Sides(0.1, 0.5)) == "−1.000e-01 / +5.000e-01"
        assert sides_text(Sides(0.0, 0.5)) == "−0.000e+00 / +5.000e-01"

    def test_equal_text_is_the_plain_format(self):
        v = math.pi
        assert sides_text(Sides(v, v)) == f"{v:.3e}"


class TestIsBaseTolerance:
    """A test's base tolerance is ONE finite number: what ``parse_sides``
    reads as a number, and nothing else."""

    @pytest.mark.parametrize(
        "ok", [0, 0.0, -0.0, 1e-4, -1, -0.5, 10**300, 5e-324], ids=repr
    )
    def test_a_finite_number_is_one_zero_and_negative_included(self, ok):
        from dstf.comparison.sides import is_base_tolerance

        assert is_base_tolerance(ok) is True

    @pytest.mark.parametrize(
        "bad",
        [
            float("nan"),
            float("inf"),
            -float("inf"),
            10**400,
            -(10**400),
            True,
            False,
            "0.5",
            "abc",
            [1],
            {},
            {"lower": 0.1, "upper": 0.1},
            None,
        ],
        ids=repr,
    )
    def test_anything_else_is_not(self, bad):
        from dstf.comparison.sides import is_base_tolerance

        assert is_base_tolerance(bad) is False

    def test_a_numpy_scalar_is_judged_as_the_number_it_is(self):
        import numpy as np

        from dstf.comparison.sides import is_base_tolerance

        assert is_base_tolerance(np.float64(0.5)) and is_base_tolerance(np.int32(2))
        for bad in (np.float64("nan"), np.float32("inf"), np.bool_(True)):
            assert not is_base_tolerance(bad)
