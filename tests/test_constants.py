"""The constants table, and what happens when it cannot answer.

Refusal is the interesting half. Falling back to the contiguous-US figure for an
Alaskan household produces an answer that is plausible, wrong, and
indistinguishable from a right one -- the exact failure this project exists to
attack. So these tests spend more effort on the refusals than on the lookups.

The lookups themselves are proven against the microdata's own per-household
columns by `python -m second_budget.validate.layer_c_constants`, which reports
100.000% on the minimum benefit and shelter cap and explains every one of the 11
residual households as a disagreement between two columns of the file.
"""

from __future__ import annotations

import pytest

from second_budget.engine.constants import (
    OutOfCoverage,
    UnknownState,
    for_region,
    for_state,
    normalise_state,
    region_for_state,
)


@pytest.fixture(scope="module")
def contiguous():
    return for_state("Ohio")


# -- the published schedule -------------------------------------------------


@pytest.mark.parametrize(
    ("size", "expected"),
    [(1, 291), (2, 535), (3, 766), (4, 973), (5, 1155), (6, 1386), (7, 1532), (8, 1751)],
)
def test_the_maximum_allotment_matches_table_f5(contiguous, size, expected) -> None:
    assert contiguous.max_allotment(size) == expected


def test_households_larger_than_the_table_add_a_fixed_amount_each(contiguous) -> None:
    """Table F.5 stops at eight and then says "+219 each additional person"."""
    assert contiguous.max_allotment(9) == 1751 + 219
    assert contiguous.max_allotment(12) == 1751 + 219 * 4


@pytest.mark.parametrize(
    ("size", "expected"),
    [(1, 198), (2, 198), (3, 198), (4, 208), (5, 244), (6, 279), (7, 279), (11, 279)],
)
def test_the_standard_deduction_bands_sizes_the_way_the_schedule_does(
    contiguous, size, expected
) -> None:
    """Table F.3 bands 1-3 together and lumps everything from 6 upward."""
    assert contiguous.standard_deduction(size) == expected


def test_the_minimum_benefit_is_a_constant_not_a_rule(contiguous) -> None:
    """Conflating the two was a real bug.

    The microdata records MINIMUM_BEN for households of every size; returning 0
    for larger ones disagreed with the file on 10,617 of them. Whether the floor
    *applies* belongs with the allotment calculation.
    """
    assert contiguous.minimum_benefit() == 23


def test_the_shelter_cap_lifts_for_an_elderly_or_disabled_household(contiguous) -> None:
    assert contiguous.shelter_cap(has_elderly_or_disabled_member=False) == 672.0
    # None, not zero. A cap of zero would wipe out the deduction entirely.
    assert contiguous.shelter_cap(has_elderly_or_disabled_member=True) is None


# -- refusal ----------------------------------------------------------------


@pytest.mark.parametrize("state", ["Alaska", "Hawaii", "Guam", "Virgin Islands"])
def test_states_on_a_separate_schedule_are_refused_not_approximated(state) -> None:
    constants = for_state(state)
    assert constants.covered is False
    with pytest.raises(OutOfCoverage) as refusal:
        constants.max_allotment(2)
    assert state.split()[0] in str(refusal.value)
    assert "snapqcdata.net" in str(refusal.value)


def test_the_alaska_refusal_names_the_actual_reason() -> None:
    """Not "unsupported" -- the specific thing that is missing."""
    with pytest.raises(OutOfCoverage) as refusal:
        for_state("Alaska").standard_deduction(1)
    assert "three benefit regions" in str(refusal.value)


def test_illinois_is_refused_because_its_deduction_is_unsourced() -> None:
    """861 of 861 Illinois households sit $7 below the published schedule.

    The value is not copied out of the microdata, because a table read off the
    data and then checked against that same data is not evidence.
    """
    constants = for_state("Illinois")
    assert constants.covered is False
    with pytest.raises(OutOfCoverage) as refusal:
        constants.standard_deduction(1)
    assert "$7 below the published contiguous schedule" in str(refusal.value)


def test_a_fiscal_year_with_no_transcribed_schedule_is_refused() -> None:
    with pytest.raises(OutOfCoverage) as refusal:
        for_region("contiguous", fiscal_year=2019)
    assert "no transcribed schedule" in str(refusal.value)


# -- the state a household actually named -----------------------------------


@pytest.mark.parametrize(
    ("written", "expected"),
    [("IL", "Illinois"), ("il", "Illinois"), ("Illinois", "Illinois"),
     ("  illinois ", "Illinois"), ("OH", "Ohio"), ("DC", "District of Columbia")],
)
def test_a_state_is_recognised_however_it_was_written(written, expected) -> None:
    assert normalise_state(written) == expected


def test_the_postal_code_and_the_full_name_reach_the_same_verdict() -> None:
    """Without this, "IL" resolves to the covered schedule and "Illinois" is
    refused -- the same household getting two different answers depending on how
    someone typed its state."""
    assert region_for_state("IL") == region_for_state("Illinois") == "illinois"
    assert for_state("IL").covered is False


@pytest.mark.parametrize("bad", ["", "   ", "Ontario", "XX", "Puerto Rico"])
def test_something_that_is_not_a_state_is_rejected_rather_than_guessed(bad) -> None:
    with pytest.raises(UnknownState):
        normalise_state(bad)


def test_every_transcribed_region_carries_its_source(contiguous) -> None:
    assert contiguous.source_url.startswith("https://")
    assert contiguous.fiscal_year == 2024
