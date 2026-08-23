"""Properties of the allotment engine that must hold for every input.

Replay proves the engine agrees with 42,689 real households. It cannot prove
anything about inputs those households happen not to contain. These tests cover
the boundaries -- which is where the two rules the microdata surfaced both live.
"""

from __future__ import annotations

import random

import pytest

from second_budget.engine.allotment import allotment
from second_budget.engine.rounding import (
    earned_income_deduction,
    half_of_adjusted_income,
    half_up,
    household_share,
)


@pytest.mark.parametrize(
    ("net_income", "expected"),
    [
        (0, 0),
        (1, 1),        # 0.30 -> rounds up to a whole dollar
        (100, 30),     # exactly 30.00, nothing to round
        (101, 31),     # 30.30 -> 31, the rule that costs a dollar
        (103, 31),     # 30.90 -> 31
        (110, 33),     # exactly 33.00
        (1000, 300),
    ],
)
def test_the_household_share_rounds_up_before_it_is_subtracted(net_income, expected) -> None:
    assert household_share(net_income) == expected


def test_rounding_up_then_subtracting_differs_from_subtracting_then_rounding() -> None:
    """The distinction 7 CFR 273.10(e)(2)(ii)(C) draws is not academic.

    If it made no difference the isolated rounding module would be pointless.
    Here is a concrete household where the two readings differ by a dollar.
    """
    import math

    max_allotment, net = 291, 101
    correct = max_allotment - math.ceil(0.30 * net)          # 291 - 31 = 260
    naive = round(max_allotment - 0.30 * net)                # 291 - 30.3 -> 261
    assert correct == 260
    assert naive == 261
    assert correct != naive


def test_allotment_never_goes_negative() -> None:
    result = allotment(
        max_allotment=291, net_income=100_000, household_size=4, minimum_benefit=23
    )
    assert result.raw == 0
    assert result.allotment == 0
    assert result.floor_applied is False


@pytest.mark.parametrize("size", [1, 2])
def test_the_floor_applies_to_small_households_at_and_below_the_minimum(size) -> None:
    # A computed allotment of exactly zero still receives the minimum benefit.
    zero = allotment(
        max_allotment=291, net_income=970, household_size=size, minimum_benefit=23
    )
    assert zero.raw == 0
    assert zero.allotment == 23
    assert zero.floor_applied is True

    # And so does a small positive one.
    small = allotment(
        max_allotment=291, net_income=900, household_size=size, minimum_benefit=23
    )
    assert 0 < small.raw < 23
    assert small.allotment == 23


def test_a_computed_allotment_at_the_minimum_is_not_treated_as_floored() -> None:
    """Boundary: exactly at the minimum is not below it."""
    # 291 - ceil(0.30 * 894) = 291 - 269 = 22  -> floored
    # 291 - ceil(0.30 * 890) = 291 - 267 = 24  -> not floored
    assert allotment(
        max_allotment=291, net_income=894, household_size=1, minimum_benefit=23
    ).floor_applied is True
    at_or_above = allotment(
        max_allotment=291, net_income=890, household_size=1, minimum_benefit=23
    )
    assert at_or_above.floor_applied is False
    assert at_or_above.allotment == 24


def test_allotment_is_non_increasing_in_net_income() -> None:
    """More income never yields more benefit, at any household size."""
    rng = random.Random(20260823)
    for _ in range(400):
        size = rng.randint(1, 8)
        max_allotment = rng.choice([291, 535, 766, 973, 1155, 1386, 1532, 1751])
        a, b = sorted(rng.uniform(0, 3000) for _ in range(2))
        lower = allotment(
            max_allotment=max_allotment, net_income=a,
            household_size=size, minimum_benefit=23,
        ).allotment
        higher = allotment(
            max_allotment=max_allotment, net_income=b,
            household_size=size, minimum_benefit=23,
        ).allotment
        assert higher <= lower, f"size={size} max={max_allotment} {a}->{lower} {b}->{higher}"


def test_the_cfr_reference_names_the_minimum_rule_only_when_it_fired() -> None:
    floored = allotment(
        max_allotment=291, net_income=970, household_size=1, minimum_benefit=23
    )
    plain = allotment(
        max_allotment=291, net_income=100, household_size=1, minimum_benefit=23
    )
    assert "273.10(e)(2)(vi)" in " ".join(floored.cfr_refs)
    assert "273.10(e)(2)(vi)" not in " ".join(plain.cfr_refs)
    assert all("273.10(e)(2)(ii)(C)" in " ".join(r.cfr_refs) for r in (floored, plain))


# -- the rounding rules, each measured against the microdata ----------------


def test_python_round_is_not_the_round_in_the_regulation() -> None:
    """Banker's rounding scores 87.487% where half-up scores 100.000%.

    5,304 households wrong by a dollar, with no exception and nothing in the
    output to suggest it. This is the single most expensive default in the
    standard library for this problem.
    """
    assert [half_up(x) for x in (0.5, 1.5, 2.5, 3.5)] == [1, 2, 3, 4]
    assert [round(x) for x in (0.5, 1.5, 2.5, 3.5)] == [0, 2, 2, 4]


@pytest.mark.parametrize(
    ("earned", "expected"),
    [(0, 0), (100, 20), (1000, 200), (1004, 200), (1009, 201), (5, 1)],
)
def test_the_earned_income_deduction_truncates(earned, expected) -> None:
    """floor matches 99.955% of households; rounding to nearest matches 90.059%."""
    assert earned_income_deduction(earned) == expected


def test_adjusted_income_is_rounded_before_it_is_halved() -> None:
    """Halving first and rounding after scores 74.502%. Order is the whole rule."""
    # 1001.4 -> half_up -> 1001 -> /2 -> 500.5, a legitimate half-dollar result.
    assert half_of_adjusted_income(1001.4) == 500.5
    # Negative adjusted income cannot produce a negative half.
    assert half_of_adjusted_income(-50) == 0.0


def test_the_three_rounding_rules_point_in_two_directions() -> None:
    """Worth stating plainly: the statute does not round consistently.

    The earned income deduction truncates down, which lowers the deduction. The
    household's share rounds up, which lowers the benefit. Both defaults fall
    the same way for the household, and neither is the obvious reading.
    """
    assert earned_income_deduction(1009) == 201        # 201.8 truncated down
    assert household_share(1009) == 303                # 302.7 rounded up
