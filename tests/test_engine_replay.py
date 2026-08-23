"""Replay the allotment engine against real federal households.

Runs on a committed 2,000-household sample (seeded draw from the FY2024 USDA
SNAP QC public-use file), so a fresh clone can prove the claim with no download
and no credentials. ``python -m second_budget.validate.layer_a_allotment`` runs
the same computation over all 44,891 records.

The residual histogram is asserted to be EMPTY, not small. A tolerance here
would hide exactly the kind of shaped residual that found the two rules this
engine implements.
"""

from __future__ import annotations

import collections
import csv
import gzip
import pathlib

import pytest

from second_budget.engine.allotment import allotment
from second_budget.engine.rounding import household_share

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "qc_sample_2000.csv.gz"

# Alaska, Hawaii, Guam and the Virgin Islands run their own maximum-allotment
# and minimum-benefit schedules. They are out of coverage, and the engine
# refuses rather than guessing for them.
SEPARATE_BENEFIT_SCHEDULE = {"Alaska", "Hawaii", "Guam", "Virgin Islands"}


def _num(value: str | None) -> float | None:
    value = (value or "").strip()
    if value in ("", ".", "NA"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _households() -> list[dict]:
    with gzip.open(FIXTURE, "rt", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def households() -> list[dict]:
    return _households()


def test_allotment_replays_every_reviewed_household_exactly(households) -> None:
    exact = 0
    residual: collections.Counter[int] = collections.Counter()

    for row in households:
        if row["STATENAME"].strip() in SEPARATE_BENEFIT_SCHEDULE:
            continue
        benmax = _num(row["BENMAX"])
        net = _num(row["FSNETINC"])
        issued = _num(row["FSBEN"])
        size = _num(row["CERTHHSZ"])
        minimum = _num(row["MINIMUM_BEN"])
        if None in (benmax, net, issued, size, minimum):
            continue

        got = allotment(
            max_allotment=int(benmax),
            net_income=net,
            household_size=int(size),
            minimum_benefit=int(minimum),
        ).allotment
        if got == int(issued):
            exact += 1
        else:
            residual[got - int(issued)] += 1

    assert exact > 1800, f"sample degenerated: only {exact} comparable households"
    assert not residual, f"residual histogram must be empty, got {residual.most_common(5)}"


def test_the_minimum_benefit_reaches_a_computed_zero(households) -> None:
    """The rule the microdata surfaced: the floor applies at zero, not only near it."""
    floored_from_zero = [
        row
        for row in households
        if row["STATENAME"].strip() not in SEPARATE_BENEFIT_SCHEDULE
        and (b := _num(row["BENMAX"])) is not None
        and (n := _num(row["FSNETINC"])) is not None
        and (s := _num(row["CERTHHSZ"])) is not None
        and int(s) <= 2
        and b - household_share(n) <= 0
    ]
    assert floored_from_zero, "sample contains no zero-allotment household to prove the rule"

    for row in floored_from_zero:
        result = allotment(
            max_allotment=int(_num(row["BENMAX"])),
            net_income=_num(row["FSNETINC"]),
            household_size=int(_num(row["CERTHHSZ"])),
            minimum_benefit=int(_num(row["MINIMUM_BEN"])),
        )
        assert result.raw == 0
        assert result.floor_applied is True
        assert result.allotment == int(_num(row["MINIMUM_BEN"]))
        assert int(_num(row["FSBEN"])) == result.allotment


def test_the_floor_never_applies_to_larger_households(households) -> None:
    for row in households:
        size = _num(row["CERTHHSZ"])
        benmax = _num(row["BENMAX"])
        net = _num(row["FSNETINC"])
        minimum = _num(row["MINIMUM_BEN"])
        if None in (size, benmax, net, minimum) or int(size) <= 2:
            continue
        result = allotment(
            max_allotment=int(benmax),
            net_income=net,
            household_size=int(size),
            minimum_benefit=int(minimum),
        )
        assert result.floor_applied is False
