"""A reconciliation must actually reconcile.

The claim this file defends: every line the report prints can be checked by
feeding the stated value back through the engine and getting the agency's figure
out. A reconciliation that does not reconcile is worse than no report, because a
navigator would take it to a hearing.
"""

from __future__ import annotations

import dataclasses

import pytest

from second_budget.engine.budget import Household, compute
from second_budget.engine.compare import ATTRIBUTABLE, compare

HOUSEHOLD = Household(
    size=2,
    earned_income=1200.0,
    unearned_income=0.0,
    standard_deduction=198.0,
    medical_expenses=25.0,
    shelter_expenses=900.0,
    has_elderly_or_disabled_member=True,
    shelter_cap=672.0,
    max_allotment=535,
    minimum_benefit=23,
)


def test_agreement_produces_no_explanations() -> None:
    derived = compute(HOUSEHOLD).allotment
    result = compare(HOUSEHOLD, agency_allotment=derived)

    assert result.agrees
    assert result.gap == 0
    assert result.reconciliations == ()


def test_every_reconciliation_actually_reconciles() -> None:
    """The load-bearing property. Feed each required value back in and the
    agency's number must come out -- exactly, not approximately."""
    result = compare(HOUSEHOLD, agency_allotment=210)

    assert result.reconciliations, "a $263 gap must have at least one explanation"
    for line in result.reconciliations:
        adjusted = dataclasses.replace(HOUSEHOLD, **{line.field: line.required})
        assert compute(adjusted).allotment == 210, (
            f"{line.field}={line.required} was reported as reconciling but yields "
            f"{compute(adjusted).allotment}"
        )


def test_the_gap_is_signed_so_a_navigator_knows_which_way_it_runs() -> None:
    underpaid = compare(HOUSEHOLD, agency_allotment=210)
    assert underpaid.gap > 0
    assert underpaid.household_is_owed is True

    overpaid = compare(HOUSEHOLD, agency_allotment=520)
    assert overpaid.gap < 0
    assert overpaid.household_is_owed is False


def test_income_and_deductions_move_the_answer_in_opposite_directions() -> None:
    """Sanity on the direction of every explanation, not just its size."""
    result = compare(HOUSEHOLD, agency_allotment=210)
    by_field = {line.field: line for line in result.reconciliations}

    # Our derivation is higher than the agency's, so to agree with the agency
    # either income must rise or a deduction must fall.
    if "earned_income" in by_field:
        assert by_field["earned_income"].required > HOUSEHOLD.earned_income
    if "shelter_expenses" in by_field:
        assert by_field["shelter_expenses"].required < HOUSEHOLD.shelter_expenses


def test_an_impossible_explanation_is_dropped_rather_than_printed_as_a_negative() -> None:
    """No deduction can go below zero, so it cannot explain an arbitrary gap."""
    lean = dataclasses.replace(
        HOUSEHOLD, medical_expenses=5.0, dependent_care_expenses=0.0,
        child_support_paid=0.0,
    )
    # A gap far larger than the small deductions could ever account for.
    result = compare(lean, agency_allotment=10)
    fields = {line.field for line in result.reconciliations}

    assert "dependent_care_expenses" not in fields
    assert "child_support_paid" not in fields
    for line in result.reconciliations:
        assert line.required >= 0.0


def test_explanations_are_ordered_by_how_small_the_required_change_is() -> None:
    """The likeliest single explanation first, and the easiest to check."""
    result = compare(HOUSEHOLD, agency_allotment=210)
    sizes = [abs(line.difference) for line in result.reconciliations]

    assert sizes == sorted(sizes)


def test_each_explanation_names_the_paragraph_that_makes_it_matter() -> None:
    result = compare(HOUSEHOLD, agency_allotment=210)

    for line in result.reconciliations:
        assert line.cfr.startswith("7 CFR ")
        assert line.label in line.sentence()
        assert line.cfr in line.sentence()


@pytest.mark.parametrize("field", sorted(ATTRIBUTABLE))
def test_every_attributable_field_exists_on_the_household(field) -> None:
    """Guards against a field being renamed in the engine and silently dropped
    from every report."""
    assert hasattr(HOUSEHOLD, field)


def test_a_household_at_the_minimum_benefit_still_reconciles() -> None:
    """The floor makes the allotment flat, which is where a naive solver breaks."""
    poor = dataclasses.replace(HOUSEHOLD, earned_income=3000.0)
    assert compute(poor).allotment == 23  # the floor is in play

    result = compare(poor, agency_allotment=200)
    for line in result.reconciliations:
        adjusted = dataclasses.replace(poor, **{line.field: line.required})
        assert compute(adjusted).allotment == 200
