"""The one arithmetic rule that everything else rests on.

7 CFR 273.10(e)(2)(ii)(C) says the household's benefit is the maximum allotment
for its size minus 30 percent of its net monthly income, and it says the 30
percent share is **rounded up to the next whole dollar before it is subtracted**
-- not the subtraction rounded afterwards. Those two readings differ by a dollar
on a large fraction of real households, always in the same direction.

This is isolated in its own module for one reason: it is the smallest possible
edit that produces a large, *shaped* error against 44,281 real federal records,
which makes it demonstrable rather than merely stated.
"""
from __future__ import annotations

import math

CFR_ALLOTMENT = "7 CFR 273.10(e)(2)(ii)(C)"


def household_share(net_income: float) -> int:
    """The household's own contribution: 30 percent of net income, rounded UP.

    >>> household_share(0)
    0
    >>> household_share(100)      # 30.0 -> exact, no rounding to do
    30
    >>> household_share(101)      # 30.3 -> 31, not 30
    31
    """
    return math.ceil(0.30 * net_income)
