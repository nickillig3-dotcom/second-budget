"""Replay the whole budget against real federal households.

Runs on a committed 2,000-household sample (a seeded draw from the FY2024 USDA
SNAP QC public-use file), so a fresh clone can prove the claim with no download
and no credentials. ``python -m second_budget.validate.layer_a_allotment`` runs
the same computation over all 44,891 records and reports 99.993%.

The residual is asserted to be EMPTY on the sample, not small. A tolerance here
would hide exactly the kind of shaped residual that produced every rule in the
engine.
"""

from __future__ import annotations

import collections
import csv
import gzip
import pathlib

import pytest

from second_budget.engine.budget import compute
from second_budget.validate.qc_adapter import STAGE_FOR_COLUMN, household, outcomes

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "qc_sample_2000.csv.gz"


@pytest.fixture(scope="module")
def replayed() -> list[tuple[dict, object, dict]]:
    """Every usable household in the sample, with its engine result."""
    out = []
    with gzip.open(FIXTURE, "rt", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            facts = household(row)
            reviewed = outcomes(row) if facts is not None else None
            if facts is None or reviewed is None:
                continue
            out.append((row, compute(facts), reviewed))
    return out


def test_the_sample_did_not_degenerate(replayed) -> None:
    assert len(replayed) > 1800, f"only {len(replayed)} usable households in the fixture"


@pytest.mark.parametrize("column", sorted(STAGE_FOR_COLUMN))
def test_every_budget_stage_matches_the_reviewed_value(replayed, column) -> None:
    stage_name = STAGE_FOR_COLUMN[column]
    residual: collections.Counter[float] = collections.Counter()

    for _row, result, reviewed in replayed:
        got = round(result.stage(stage_name).value, 2)
        want = round(reviewed[column], 2)
        if got != want:
            residual[round(got - want, 2)] += 1

    assert not residual, (
        f"{stage_name} vs {column}: residual must be empty, "
        f"got {residual.most_common(5)}"
    )


def test_net_income_is_computed_and_never_borrowed(replayed) -> None:
    """Guards the claim that this is a test of the budget, not of one subtraction.

    If the engine ever read FSNETINC instead of deriving it, this identity would
    hold trivially. It holds because every deduction stage is right.
    """
    for _row, result, _reviewed in replayed:
        gross = result.stage("gross_income").value
        deductions = result.stage("total_deductions").value
        assert result.stage("net_income").value == max(0.0, gross - deductions)


def test_the_minimum_benefit_reaches_a_computed_zero(replayed) -> None:
    """The rule the microdata surfaced: the floor applies at zero, not only near it."""
    from_zero = [
        (result, reviewed)
        for _row, result, reviewed in replayed
        if result.floor_applied and result.stage("allotment_before_minimum").value == 0
    ]
    assert from_zero, "sample contains no zero-allotment household to prove the rule"

    for result, reviewed in from_zero:
        assert result.allotment > 0
        assert result.allotment == reviewed["FSBEN"]


def test_the_floor_never_applies_to_larger_households(replayed) -> None:
    for row, result, _reviewed in replayed:
        if int(float(row["CERTHHSZ"])) > 2:
            assert result.floor_applied is False


def test_the_homeless_standard_deduction_replaces_the_shelter_stage(replayed) -> None:
    """HOMEDED = 3 zeroes the excess-shelter stage and deducts a flat amount instead.

    Missing the second half of that rule cost exactly ceil(0.30 * 180) = 54
    dollars of benefit on 38 households before it was found.
    """
    homeless = [
        result
        for row, result, _reviewed in replayed
        if row.get("HOMEDED", "").strip() == "3"
    ]
    assert homeless, "sample contains no household taking the standard homeless deduction"

    for result in homeless:
        assert result.stage("excess_shelter_deduction").value == 0
        assert result.stage("homeless_shelter_deduction").value > 0


def test_every_stage_cites_a_regulation(replayed) -> None:
    """A number that cannot name its paragraph cannot go into a filing."""
    _row, result, _reviewed = replayed[0]
    for stage in result.stages:
        assert stage.cfr.startswith("7 CFR "), f"{stage.name} has no citation"
