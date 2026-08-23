"""Localise a disagreement: which input would have to be different, and by how much.

A notice of action gives a household one number. It does not show the budget
behind it. So the honest question is not "which stage did the agency get wrong"
-- a single final figure cannot answer that -- but:

    For this notice to be correct, what would have to be true about this
    household that the household says is not true?

That question *is* answerable. The budget is monotone in every input, so for each
one there is at most one value that reconciles the two figures, and it can be
found exactly. The result is a short list of concrete, checkable statements:

    "Your notice is right only if your rent is $877 lower than you told me."
    "Your notice is right only if you earn $2,927 more than you told me."

A navigator can take that list to a hearing. It is falsifiable in a way that
"your benefit looks wrong" is not, and each line names the paragraph of 7 CFR
that makes the input matter.

Two honesty constraints are built in rather than left to the caller.

**Infeasible explanations are dropped, not rendered as huge numbers.** If no
non-negative rent reconciles the figures, then rent cannot be the explanation and
saying so is more useful than printing a negative.

**A reconciliation is not an accusation.** The agency may hold facts the
household did not mention, and the household may be misremembering. The report
says what would have to be true, and stops there.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from .budget import Household, compute

#: Which inputs a disagreement can be attributed to, with the paragraph that
#: makes each one matter. Household size and the elderly/disabled flag are
#: deliberately absent: they are categorical, so "how much would it change"
#: is not a question, and they are checked separately.
ATTRIBUTABLE = {
    "earned_income": ("earned income", "7 CFR 273.9(b)", "monthly"),
    "unearned_income": ("unearned income", "7 CFR 273.9(b)", "monthly"),
    "shelter_expenses": ("shelter costs", "7 CFR 273.9(d)(6)", "monthly"),
    "medical_expenses": ("medical deduction", "7 CFR 273.9(d)(3)", "monthly"),
    "dependent_care_expenses": ("dependent care costs", "7 CFR 273.9(d)(4)", "monthly"),
    "child_support_paid": ("child support paid", "7 CFR 273.9(d)(5)", "monthly"),
}

#: No input is searched beyond this. A household with $100,000 of monthly rent is
#: not the explanation for anything.
SEARCH_CEILING = 100_000.0
CENT = 0.01


@dataclass(frozen=True)
class Reconciliation:
    """One input, and the value that would make the agency's figure correct."""

    field: str
    label: str
    cfr: str
    stated: float
    required: float

    @property
    def difference(self) -> float:
        return self.required - self.stated

    @property
    def direction(self) -> str:
        return "higher" if self.difference > 0 else "lower"

    def sentence(self) -> str:
        return (
            f"the notice is correct only if {self.label} is "
            f"${abs(self.difference):,.2f} {self.direction} than stated "
            f"(${self.required:,.2f} rather than ${self.stated:,.2f}) -- {self.cfr}"
        )


@dataclass(frozen=True)
class Disagreement:
    """The gap, and every single-input explanation for it."""

    derived: int
    stated_by_agency: int
    reconciliations: tuple[Reconciliation, ...]

    @property
    def gap(self) -> int:
        return self.derived - self.stated_by_agency

    @property
    def agrees(self) -> bool:
        return self.gap == 0

    @property
    def household_is_owed(self) -> bool:
        """True when our derivation is higher, i.e. the household is underpaid."""
        return self.gap > 0


def _allotment_with(household: Household, field: str, value: float) -> int:
    return compute(dataclasses.replace(household, **{field: value})).allotment


def _solve(household: Household, field: str, target: int) -> float | None:
    """The value of ``field`` that yields ``target``, or ``None`` if none does.

    The allotment is monotone non-increasing in every attributable input except
    the deductions, where it is non-decreasing -- so a bisection converges. It is
    also a step function, so the search targets the *boundary*: the smallest
    value whose allotment reaches the target from the correct side.
    """
    low, high = 0.0, SEARCH_CEILING
    at_low = _allotment_with(household, field, low)
    at_high = _allotment_with(household, field, high)

    if at_low == target:
        return low
    if at_high == target:
        return high
    # The target must lie strictly between the endpoints for a root to exist.
    if not (min(at_low, at_high) < target < max(at_low, at_high)):
        return None

    increasing = at_high > at_low
    for _ in range(200):
        mid = (low + high) / 2
        value = _allotment_with(household, field, mid)
        if value == target:
            return round(mid, 2)
        if (value < target) == increasing:
            low = mid
        else:
            high = mid
        if high - low < CENT:
            break

    for candidate in (round(low, 2), round(high, 2), round((low + high) / 2, 2)):
        if _allotment_with(household, field, candidate) == target:
            return candidate
    return None


def compare(household: Household, *, agency_allotment: int) -> Disagreement:
    """Derive the budget and localise any disagreement with the agency."""
    derived = compute(household).allotment
    if derived == agency_allotment:
        return Disagreement(derived=derived, stated_by_agency=agency_allotment,
                            reconciliations=())

    found: list[Reconciliation] = []
    for field, (label, cfr, _unit) in ATTRIBUTABLE.items():
        stated = float(getattr(household, field))
        required = _solve(household, field, agency_allotment)
        if required is None or abs(required - stated) < CENT:
            continue
        found.append(
            Reconciliation(field=field, label=label, cfr=cfr,
                           stated=stated, required=required)
        )

    # Smallest required change first: the likeliest single explanation, and the
    # easiest one for a navigator to check against a payslip or a lease.
    found.sort(key=lambda r: abs(r.difference))
    return Disagreement(derived=derived, stated_by_agency=agency_allotment,
                        reconciliations=tuple(found))
