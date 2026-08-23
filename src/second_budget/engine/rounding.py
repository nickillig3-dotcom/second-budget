"""Rounding, isolated -- because three different rules live in one statute.

SNAP's benefit calculation rounds in three different directions, and each one is
worth a dollar to a household every month:

  * the earned income deduction is **truncated down**   (7 CFR 273.9(d)(2))
  * half of adjusted income is rounded **half-up**      (7 CFR 273.9(d)(6)(ii))
  * the household's 30% share is rounded **up**         (7 CFR 273.10(e)(2)(ii)(C))

They are collected here rather than inlined for two reasons. The first is that a
reader can see all three at once and check them against the regulation. The
second is that this is the smallest edit that produces a large, *shaped* error
against 42,689 real federal households -- which makes the rules demonstrable
rather than merely asserted.

**Python's built-in ``round`` is not the ``round`` in the regulation.** It is
banker's rounding (half-to-even), so ``round(0.5) == 0`` and ``round(1.5) == 2``.
Using it for the half-of-adjusted-income step scores 87.487% against the
microdata instead of 100.000% -- 5,304 households wrong by a dollar, silently,
with no exception and no clue in the output. See ``half_up``.
"""

from __future__ import annotations

import math

CFR_EARNED_DEDUCTION = "7 CFR 273.9(d)(2)"
CFR_SHELTER_HALF = "7 CFR 273.9(d)(6)(ii)"
CFR_ALLOTMENT = "7 CFR 273.10(e)(2)(ii)(C)"
CFR_MEDICAL = "7 CFR 273.9(d)(3)"

#: Only medical expenses ABOVE this monthly threshold are deductible, and only
#: for elderly or disabled members.
MEDICAL_THRESHOLD = 35.0


def half_up(value: float) -> int:
    """Round half away from zero, the way the regulation means "round".

    Contrast with the built-in, which rounds half to even:

    >>> [half_up(x) for x in (0.5, 1.5, 2.5)]
    [1, 2, 3]
    >>> [round(x) for x in (0.5, 1.5, 2.5)]
    [0, 2, 2]
    """
    if value < 0:
        return -math.floor(-value + 0.5)
    return math.floor(value + 0.5)


def earned_income_deduction(earned_income: float) -> int:
    """20 percent of earned income, **truncated** -- never rounded up.

    Measured against the microdata: floor matches 42,513 of 42,532 households
    (99.955%); rounding to nearest matches only 90.059%, missing low by exactly
    one dollar on 4,209 of them.

    >>> earned_income_deduction(1000)
    200
    >>> earned_income_deduction(1004)     # 200.8 -> 200, not 201
    200
    """
    return math.floor(0.20 * earned_income)


def half_of_adjusted_income(adjusted_income: float) -> float:
    """Half of adjusted income, with the whole-dollar rounding applied *first*.

    The codebook is explicit about the order: round the adjusted income to a
    whole dollar, then halve it -- so the result can carry a half-dollar. Halving
    first and then rounding scores 74.502%.
    """
    return max(0.0, half_up(adjusted_income) / 2)


def household_share(net_income: float) -> int:
    """The household's own contribution: 30 percent of net income, rounded UP.

    Rounded up *before* it is subtracted, not the subtraction rounded after.

    >>> household_share(0)
    0
    >>> household_share(100)      # exactly 30.00, nothing to round
    30
    >>> household_share(101)      # 30.30 -> 31, not 30
    31
    """
    return math.ceil(0.30 * net_income)


def medical_deduction(
    gross_medical_expenses: float, *, threshold: float = MEDICAL_THRESHOLD
) -> float:
    """The deductible part of a household's medical expenses.

    Only the amount **above $35 a month** is deductible, and only for elderly or
    disabled members (7 CFR 273.9(d)(3)).

    This exists because of where the two numbers come from. The federal
    microdata reports ``FSMEDEXP`` already net of the threshold -- the codebook
    calls it "allowable medical expenses in excess of $35" -- so replay feeds the
    engine a deduction. A household, on the other hand, says "she spent sixty
    dollars on her inhaler", which is a *gross* expense. Converting one to the
    other is a rule in the regulation, so it lives here rather than being left to
    the model to do in its head.

    >>> medical_deduction(60)
    25.0
    >>> medical_deduction(30)
    0.0
    """
    return max(0.0, gross_medical_expenses - threshold)
