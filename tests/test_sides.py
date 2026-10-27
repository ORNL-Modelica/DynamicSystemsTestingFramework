"""Sides: a tolerance is a number or a lower/upper pair — one reader, one writer."""

import json
import math

import pytest

from dstf.comparison.sides import (
    Sides,
    as_written,
    base_tolerance_refused,
    parse_sides,
    sides_text,
)


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
            f"got {json.dumps(bad)}"
        )

    @pytest.mark.parametrize(
        "bad,shown",
        [
            (True, "true"),
            (None, "null"),
            ("abc", '"abc"'),
            ({"lower": 1}, '{"lower": 1}'),
            ({"lower": 1, "upper": None}, '{"lower": 1, "upper": null}'),
            (float("nan"), "NaN"),
            (-float("inf"), "-Infinity"),
            ([1, "é"], '[1, "é"]'),
        ],
        ids=repr,
    )
    def test_the_value_reads_as_the_json_file_holds_it(self, bad, shown):
        """Not as Python spells it (``True``, ``None``, ``{'lower': 1}``):
        the reader is looking at a JSON file. The same text a refused base
        tolerance shows (`base_tolerance_refused`)."""
        with pytest.raises(ValueError) as exc:
            parse_sides(bad, "tolerance")
        assert str(exc.value).endswith(f", got {shown}")
        assert base_tolerance_refused("X", bad).endswith(f"(X is {shown})")

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
        shown = json.dumps(bad)[:60] + "…"
        assert len(json.dumps(bad)) > 60
        assert str(exc.value) == (
            f'tolerance must be a number or {{"lower": …, "upper": …}}, got {shown}'
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

    def test_a_zero_side_reads_by_its_side_not_its_sign(self):
        """``-0.0`` is zero: its edge reads as ``0.0``'s does."""
        assert sides_text(Sides(-0.0, 0.5)) == "−0.000e+00 / +5.000e-01"
        assert sides_text(Sides(0.5, -0.0)) == "−5.000e-01 / +0.000e+00"
        assert sides_text(Sides(-0.0, 0.5), "g") == sides_text(Sides(0.0, 0.5), "g")

    def test_equal_negative_sides_print_the_minus_as_unequal_ones_do(self):
        assert sides_text(Sides(-0.05, -0.05)) == "−5.000e-02"
        assert sides_text(Sides(-0.5, -0.5), "g") == "−0.5"
        # The exponent's sign stays as the format wrote it.
        assert sides_text(Sides(0.05, 0.05)) == "5.000e-02"

    def test_equal_text_is_the_plain_format(self):
        v = math.pi
        assert sides_text(Sides(v, v)) == f"{v:.3e}"

    def test_equal_zero_sides_read_as_zero_whatever_the_sign_bit(self):
        """``-0.0`` is zero for equal sides too: no minus."""
        for sides in (Sides(-0.0, -0.0), Sides(0.0, -0.0), Sides(-0.0, 0.0)):
            assert sides_text(sides) == "0.000e+00" == sides_text(Sides(0.0, 0.0))
            assert sides_text(sides, "g") == "0"


class TestTheRefusalSentence:
    """`base_tolerance_refused`: the rule, where the value is written, and
    the value as the JSON file holds it — not Python's spelling of it."""

    RULE = "the base tolerance must be a number"
    PAIR = "; a lower/upper pair belongs on a criterion"

    @pytest.mark.parametrize(
        "value,shown",
        [
            (True, "true"),
            (False, "false"),
            ("abc", '"abc"'),
            ("0.5", '"0.5"'),
            ("é", '"é"'),
            ([1], "[1]"),
            ([True, None], "[true, null]"),
            (float("nan"), "NaN"),
            (float("inf"), "Infinity"),
            (-float("inf"), "-Infinity"),
        ],
        ids=repr,
    )
    def test_a_value_reads_as_json_and_only_a_dict_is_sent_to_a_criterion(
        self, value, shown
    ):
        from dstf.comparison.sides import base_tolerance_refused

        assert base_tolerance_refused("X", value) == f"{self.RULE} (X is {shown})"

    @pytest.mark.parametrize(
        "value,shown",
        [
            ({"lower": 0.1, "upper": 0.2}, '{"lower": 0.1, "upper": 0.2}'),
            ({"lower": 0.1}, '{"lower": 0.1}'),
            ({}, "{}"),
        ],
        ids=repr,
    )
    def test_a_dict_is_told_where_a_pair_belongs(self, value, shown):
        from dstf.comparison.sides import base_tolerance_refused

        assert base_tolerance_refused("X", value) == (
            f"{self.RULE}{self.PAIR} (X is {shown})"
        )

    def test_a_long_value_is_cut_at_60_characters(self):
        from dstf.comparison.sides import base_tolerance_refused

        at_60 = "a" * 58  # the two quotes make 60
        assert base_tolerance_refused("X", at_60) == f'{self.RULE} (X is "{at_60}")'
        assert base_tolerance_refused("X", "a" * 59) == (
            f'{self.RULE} (X is "{"a" * 59}…)'
        )
        assert base_tolerance_refused("X", 10**400) == (
            f"{self.RULE} (X is 1{'0' * 59}…)"
        )

    def test_a_value_json_cannot_write_falls_back_to_its_repr(self):
        from dstf.comparison.sides import base_tolerance_refused

        assert base_tolerance_refused("X", {1, 2} - {2}) == f"{self.RULE} (X is {{1}})"
        assert base_tolerance_refused("X", 1 + 2j) == f"{self.RULE} (X is (1+2j))"
        cycle: list = []
        cycle.append(cycle)
        assert base_tolerance_refused("X", cycle) == f"{self.RULE} (X is [[...]])"


class TestIsBaseTolerance:
    """A test's base tolerance is ONE finite number: what ``parse_sides``
    reads as a number, and nothing else."""

    @pytest.mark.parametrize("ok", [0, 0.0, -0.0, 1e-4, 10**300, 5e-324], ids=repr)
    def test_a_finite_number_of_0_or_more_is_one(self, ok):
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
            # every nrmse test would fail against a negative ceiling
            -1,
            -0.5,
            -5e-324,
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

    def test_a_negative_number_is_refused_as_one(self):
        from dstf.comparison.sides import base_tolerance_refused

        assert base_tolerance_refused("--tolerance", -1.0) == (
            "the base tolerance must be 0 or more (--tolerance is -1.0)"
        )

    def test_a_numpy_scalar_is_judged_as_the_number_it_is(self):
        import numpy as np

        from dstf.comparison.sides import is_base_tolerance

        assert is_base_tolerance(np.float64(0.5)) and is_base_tolerance(np.int32(2))
        for bad in (np.float64("nan"), np.float32("inf"), np.bool_(True)):
            assert not is_base_tolerance(bad)
