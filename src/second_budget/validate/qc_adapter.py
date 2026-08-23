"""Map one USDA QC record onto the engine's ``Household``.

One adapter, used by both the committed-sample tests and the full-file run, so
the two can never drift apart and quietly measure different things.

Column meanings are taken from the FY2024 SNAP QC Technical Documentation
(``FY-2024-Tech-Doc.pdf``, Chapter V codebook), not inferred from their names.
The distinction that matters most is origin: a column marked **C** is
*constructed* -- the reviewed computation -- while **R** is *reported*, what the
household or agency actually stated. Confusing the two makes a claim about the
wrong thing.
"""

from __future__ import annotations

from ..engine.budget import Household

#: Alaska, Hawaii, Guam and the Virgin Islands run their own maximum-allotment
#: and minimum-benefit schedules. The engine refuses for them rather than
#: guessing, so they are excluded from every measurement.
SEPARATE_BENEFIT_SCHEDULE = frozenset({"Alaska", "Hawaii", "Guam", "Virgin Islands"})

#: Everything the engine needs from a record. A row missing any of these is
#: skipped and counted, never defaulted to zero -- a defaulted deduction would
#: silently shift a household's benefit.
REQUIRED_COLUMNS = (
    "FSEARN",        # C  countable earned income
    "FSUNEARN",      # C  countable unearned income
    "FSSTDDED",      # C  standard deduction
    "FSDEPDED",      # C  dependent care deduction
    "FSMEDDED",      # C  medical deduction, already net of the $35 threshold
    "FSCSDED",       # C  child support deduction
    "FSSLTEXP",      # C  calculated shelter expenses = RENT + UTIL
    "SHELCAP",       # C  maximum allowable shelter deduction, varies by region
    "FSNELDER",      # C  number of elderly members
    "FSNDIS",        # C  number of members with a disability
    "HOMEDED",       # R  homelessness indicator; 3 = taking the standard deduction
    "HOMELESS_DED",  # C  amount of the standard homeless shelter deduction
    "BENMAX",        # C  maximum allotment for this size and region
    "MINIMUM_BEN",   # C  minimum benefit amount
    "CERTHHSZ",      # C  certified unit size
)

#: Reference values a measurement compares against.
OUTCOME_COLUMNS = (
    "FSSLTDED",   # C  calculated excess shelter expense deduction
    "FSTOTDED",   # C  total deductions
    "FSNETINC",   # C  final net countable unit income
    "FSBEN",      # C  final calculated benefit
)


def number(raw: str | None) -> float | None:
    """Parse a QC cell. The public-use file writes every missing value as ``.``."""
    value = (raw or "").strip()
    if value in ("", ".", "NA"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def in_coverage(row: dict) -> bool:
    return row.get("STATENAME", "").strip() not in SEPARATE_BENEFIT_SCHEDULE


def household(row: dict) -> Household | None:
    """Build a ``Household``, or ``None`` if the record cannot support one."""
    if not in_coverage(row):
        return None
    values = {column: number(row.get(column)) for column in REQUIRED_COLUMNS}
    if any(value is None for value in values.values()):
        return None

    return Household(
        size=int(values["CERTHHSZ"]),
        earned_income=values["FSEARN"],
        unearned_income=values["FSUNEARN"],
        standard_deduction=values["FSSTDDED"],
        dependent_care_expenses=values["FSDEPDED"],
        medical_expenses=values["FSMEDDED"],
        child_support_paid=values["FSCSDED"],
        shelter_expenses=values["FSSLTEXP"],
        shelter_cap=values["SHELCAP"],
        has_elderly_or_disabled_member=values["FSNELDER"] > 0 or values["FSNDIS"] > 0,
        homeless_receiving_standard_deduction=values["HOMEDED"] == 3,
        homeless_shelter_deduction=values["HOMELESS_DED"],
        max_allotment=int(values["BENMAX"]),
        minimum_benefit=int(values["MINIMUM_BEN"]),
    )


def outcomes(row: dict) -> dict[str, float] | None:
    """The reviewed values this record is measured against."""
    values = {column: number(row.get(column)) for column in OUTCOME_COLUMNS}
    if any(value is None for value in values.values()):
        return None
    return values


#: Which engine stage each reference column should equal.
STAGE_FOR_COLUMN = {
    "FSSLTDED": "excess_shelter_deduction",
    "FSTOTDED": "total_deductions",
    "FSNETINC": "net_income",
    "FSBEN": "allotment",
}
