"""Every citation the engine emits must point at text that says what we claim.

This file exists because the citations were wrong and nothing caught it.

``CFR_ALLOTMENT`` said ``273.10(e)(2)(ii)(C)`` — which is the *minimum benefit*
paragraph — while the allotment formula and its rounding rule are at
``(e)(2)(ii)(A)``. And ``CFR_MINIMUM`` said ``(e)(2)(vi)``, a paragraph that
(e)(2)(ii)(A) merely lists as an exception. Both were plausible, both were
wrong, and both would have gone into a fair-hearing request for a hearing
officer to look up.

The project's whole argument is that a citation must be checkable rather than
asserted. That argument has to apply to the engine's own constants first.

Each case below pins a constant to a phrase that must appear in the cited
section. The phrases are short and distinctive, chosen from the regulation
itself — long enough to identify the rule, short enough to survive an eCFR
reflow.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from second_budget.engine import budget, rounding
from second_budget.engine.allotment import CFR_MINIMUM
from second_budget.memory.statute_store import StatuteStore

#: (constant, the text the cited paragraph must contain)
PINS = [
    (
        rounding.CFR_ALLOTMENT,
        "reduced by 30 percent of the household's net monthly income",
    ),
    (
        rounding.CFR_ALLOTMENT,
        "round the 30 percent of net income up to the nearest higher dollar",
    ),
    (
        CFR_MINIMUM,
        "one-person and two-person households shall receive minimum monthly allotments",
    ),
    (
        budget.CFR_MINIMUM_BENEFIT,
        "one-person and two-person households shall receive minimum monthly allotments",
    ),
    (rounding.CFR_EARNED_DEDUCTION, "Twenty percent of gross earned income"),
    (rounding.CFR_MEDICAL, "in excess of $35 per month"),
    (budget.CFR_SHELTER_DEDUCTION, "shelter"),
    (rounding.CFR_SHELTER_HALF, "in excess of 50 percent of the household"),
    (budget.CFR_HOMELESS_SHELTER, "homeless shelter deduction"),
    (budget.CFR_STANDARD_DEDUCTION, "Standard deduction"),
    (budget.CFR_DEPENDENT_CARE,
     "Payments for dependent care when necessary for a household member"),
    (budget.CFR_CHILD_SUPPORT, "child support"),
]


@pytest.fixture(scope="module")
def store() -> StatuteStore:
    s = StatuteStore()
    asyncio.run(s.initialize())
    return s


def _section_of(citation: str) -> str:
    """``7 CFR 273.10(e)(2)(ii)(A)`` -> ``7 CFR 273.10``.

    The index is section-level, deliberately: eCFR publishes no paragraph paths
    and reconstructing them left 187 duplicate citations across 2,418
    paragraphs. So a citation is checked against the section it names.
    """
    match = re.match(r"(7 CFR \d+\.\d+)", citation)
    assert match, f"{citation!r} is not shaped like a CFR citation"
    return match.group(1)


@pytest.mark.parametrize(("citation", "phrase"), PINS, ids=[f"{c}::{p[:28]}" for c, p in PINS])
def test_the_cited_section_actually_says_this(store, citation, phrase) -> None:
    text = store.section_text(_section_of(citation))
    assert text is not None, f"{_section_of(citation)} is not in the statute index"
    assert phrase in text, (
        f"{citation} is cited for {phrase!r}, but {_section_of(citation)} does not "
        f"contain that text"
    )


def test_the_allotment_and_the_minimum_benefit_are_different_paragraphs() -> None:
    """The specific mix-up that prompted this file."""
    assert rounding.CFR_ALLOTMENT != CFR_MINIMUM
    assert rounding.CFR_ALLOTMENT.endswith("(ii)(A)")
    assert CFR_MINIMUM.endswith("(ii)(C)")


def test_every_budget_stage_cites_a_section_the_index_holds(store) -> None:
    """No stage may cite a section that does not exist."""
    from second_budget.engine.budget import Household, compute

    result = compute(
        Household(size=2, earned_income=1200.0, standard_deduction=198.0,
                  shelter_expenses=900.0, shelter_cap=672.0,
                  max_allotment=535, minimum_benefit=23)
    )
    for stage in result.stages:
        section = _section_of(stage.cfr)
        assert store.section_text(section) is not None, (
            f"stage {stage.name} cites {stage.cfr}, and {section} is not a real section"
        )
