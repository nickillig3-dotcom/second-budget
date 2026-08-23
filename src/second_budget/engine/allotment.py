"""Maximum allotment, the household share, and the minimum benefit floor.

Deliberately free of I/O, dates, and configuration lookups: everything it needs
arrives as arguments, so every rule in it is decidable in a unit test.
"""
from __future__ import annotations

from dataclasses import dataclass

from .rounding import household_share

CFR_MINIMUM = "7 CFR 273.10(e)(2)(vi)"


@dataclass(frozen=True)
class AllotmentResult:
    allotment: int
    raw: int                 # before the minimum-benefit floor
    floor_applied: bool
    cfr_refs: tuple[str, ...]


def allotment(
    *,
    max_allotment: int,
    net_income: float,
    household_size: int,
    minimum_benefit: int,
) -> AllotmentResult:
    """Compute the monthly allotment.

    The minimum benefit applies to one- and two-person households only, and --
    this is the part that a natural reading misses -- it applies even when the
    computed allotment is **zero**, not only when it is merely small. Both
    conditions are visible in the federal microdata.
    """
    raw = max_allotment - household_share(net_income)
    if raw < 0:
        raw = 0
    value = raw
    floored = False
    if household_size <= 2 and raw < minimum_benefit:
        value = minimum_benefit
        floored = True
    return AllotmentResult(
        allotment=value,
        raw=raw,
        floor_applied=floored,
        cfr_refs=("7 CFR 273.10(e)(2)(ii)(C)",) + ((CFR_MINIMUM,) if floored else ()),
    )
