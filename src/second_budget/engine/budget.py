"""The SNAP budget as a pure function: facts in, a stage-by-stage result out.

No I/O, no clock, no configuration lookup, no model. Everything the computation
needs arrives as an argument, so every rule in it is decidable in a unit test and
the whole thing is replayable against 42,000 real federal households in seconds.

That is the point of the architecture. The model in this system elicits facts and
drafts prose; it never computes. A number that reaches a household's appeal comes
from here, or it does not exist.

Each stage carries the paragraph of 7 CFR it implements, so the disagreement
report and the fair-hearing packet can quote the regulation rather than
paraphrase it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .allotment import allotment
from .rounding import (
    CFR_ALLOTMENT,
    CFR_EARNED_DEDUCTION,
    CFR_SHELTER_HALF,
    earned_income_deduction,
    half_of_adjusted_income,
    half_up,
    household_share,
)

CFR_STANDARD_DEDUCTION = "7 CFR 273.9(d)(1)"
CFR_MEDICAL = "7 CFR 273.9(d)(3)"
CFR_DEPENDENT_CARE = "7 CFR 273.9(d)(4)"
CFR_CHILD_SUPPORT = "7 CFR 273.9(d)(5)"
CFR_SHELTER_DEDUCTION = "7 CFR 273.9(d)(6)"
CFR_HOMELESS_SHELTER = "7 CFR 273.9(d)(6)(i)"
CFR_NET_INCOME = "7 CFR 273.10(e)(1)(i)"
CFR_MINIMUM_BENEFIT = "7 CFR 273.10(e)(2)(vi)"


@dataclass(frozen=True)
class Stage:
    """One named step of the budget, with the paragraph that governs it."""

    name: str
    value: float
    cfr: str
    note: str = ""


@dataclass(frozen=True)
class Household:
    """The facts a SNAP budget needs. Every field is elicited and confirmed.

    Monetary fields are monthly dollars. ``shelter_expenses`` is rent or mortgage
    plus the applicable utility allowance -- assembling it is a separate step,
    because which utility standard applies is a state-by-state question.
    """

    size: int
    earned_income: float = 0.0
    unearned_income: float = 0.0
    standard_deduction: float = 0.0
    dependent_care_expenses: float = 0.0
    medical_expenses: float = 0.0          # already net of the $35 threshold
    child_support_paid: float = 0.0
    shelter_expenses: float = 0.0
    has_elderly_or_disabled_member: bool = False
    # A homeless household may take a flat standard shelter deduction instead of
    # the excess-shelter calculation. It replaces that stage rather than adding
    # to it: the excess shelter deduction goes to zero and this amount is
    # deducted instead. 7 CFR 273.9(d)(6)(i); $179.66 in FY2024, carried in the
    # microdata as 180.
    homeless_receiving_standard_deduction: bool = False
    homeless_shelter_deduction: float = 0.0
    shelter_cap: float | None = None       # None only when the cap cannot apply
    max_allotment: int = 0
    minimum_benefit: int = 0


@dataclass(frozen=True)
class BudgetResult:
    allotment: int
    stages: tuple[Stage, ...]
    floor_applied: bool = False
    by_name: dict[str, Stage] = field(default_factory=dict, compare=False)

    def stage(self, name: str) -> Stage:
        return self.by_name[name]


def compute(h: Household) -> BudgetResult:
    """Run the budget. Stage order is the order the regulation applies them."""
    stages: list[Stage] = []

    gross = h.earned_income + h.unearned_income
    stages.append(Stage("gross_income", gross, "7 CFR 273.9(b)"))

    earned_ded = earned_income_deduction(h.earned_income)
    stages.append(
        Stage("earned_income_deduction", earned_ded, CFR_EARNED_DEDUCTION,
              "20 percent of earned income, truncated")
    )
    stages.append(Stage("standard_deduction", h.standard_deduction, CFR_STANDARD_DEDUCTION))
    stages.append(Stage("dependent_care_deduction", h.dependent_care_expenses, CFR_DEPENDENT_CARE))
    stages.append(
        Stage("medical_deduction", h.medical_expenses, CFR_MEDICAL,
              "expenses above $35 per month, elderly or disabled members only")
    )
    stages.append(Stage("child_support_deduction", h.child_support_paid, CFR_CHILD_SUPPORT))

    adjusted = (
        gross
        - earned_ded
        - h.standard_deduction
        - h.dependent_care_expenses
        - h.medical_expenses
        - h.child_support_paid
    )
    stages.append(Stage("adjusted_income", adjusted, "7 CFR 273.9(d)"))

    half_net = half_of_adjusted_income(adjusted)
    stages.append(
        Stage("half_of_adjusted_income", half_net, CFR_SHELTER_HALF,
              "adjusted income rounded half-up to whole dollars, then halved")
    )
    stages.append(Stage("shelter_expenses", h.shelter_expenses, CFR_SHELTER_DEDUCTION))

    excess = max(0.0, h.shelter_expenses - half_net)
    if h.homeless_receiving_standard_deduction:
        shelter_ded: float = 0.0
        shelter_note = "zero: the standard homeless shelter deduction applies instead"
        shelter_cfr = CFR_HOMELESS_SHELTER
    elif h.has_elderly_or_disabled_member:
        shelter_ded = excess
        shelter_note = "uncapped: the household has an elderly or disabled member"
        shelter_cfr = CFR_SHELTER_DEDUCTION
    else:
        cap = h.shelter_cap
        shelter_ded = excess if cap is None else min(excess, cap)
        shelter_note = "capped" if (cap is not None and excess > cap) else "below the cap"
        shelter_cfr = CFR_SHELTER_DEDUCTION
    shelter_ded = half_up(shelter_ded)
    stages.append(Stage("excess_shelter_deduction", shelter_ded, shelter_cfr, shelter_note))

    homeless_ded = (
        h.homeless_shelter_deduction if h.homeless_receiving_standard_deduction else 0.0
    )
    stages.append(
        Stage("homeless_shelter_deduction", homeless_ded, CFR_HOMELESS_SHELTER,
              "flat standard deduction, taken instead of the excess shelter calculation")
    )

    total_deductions = (
        earned_ded
        + h.standard_deduction
        + h.dependent_care_expenses
        + h.medical_expenses
        + h.child_support_paid
        + shelter_ded
        + homeless_ded
    )
    stages.append(Stage("total_deductions", total_deductions, "7 CFR 273.9(d)"))

    net = max(0.0, gross - total_deductions)
    stages.append(Stage("net_income", net, CFR_NET_INCOME))

    stages.append(
        Stage("household_share", household_share(net), CFR_ALLOTMENT,
              "30 percent of net income, rounded up before it is subtracted")
    )

    final = allotment(
        max_allotment=h.max_allotment,
        net_income=net,
        household_size=h.size,
        minimum_benefit=h.minimum_benefit,
    )
    stages.append(Stage("allotment_before_minimum", final.raw, CFR_ALLOTMENT))
    stages.append(
        Stage("allotment", final.allotment,
              CFR_MINIMUM_BENEFIT if final.floor_applied else CFR_ALLOTMENT,
              "minimum benefit applied" if final.floor_applied else "")
    )

    return BudgetResult(
        allotment=final.allotment,
        stages=tuple(stages),
        floor_applied=final.floor_applied,
        by_name={s.name: s for s in stages},
    )
