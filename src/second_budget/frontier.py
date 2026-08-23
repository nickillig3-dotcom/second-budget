"""Which fact is needed next -- computed, never chosen by the model.

SNAP's required-fact set is not fixed. Whether medical expenses matter depends
on whether a member is elderly or disabled. Whether the excess-shelter cap
applies depends on the same fact. Whether a utility allowance is in play depends
on the household's billing arrangements and its state.

So "what should I ask next?" is a **function of what is already established**,
and that function belongs in code. This is the whole reason the elicitation loop
is a cycle rather than a form: the questions are not knowable in advance, and the
loop ends when this module returns an empty frontier -- a Python predicate, not
a model announcing that it feels finished.

Everything here is pure. ``missing_facts`` takes a set of ``FactId`` and returns
a set of ``FactId``; there is no I/O, no model, and no ordering surprise. That
makes the termination condition of a multi-agent graph unit-testable, which is
otherwise one of the harder things to assert about an agent.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .facts import FactId

#: A requirement that always applies, whatever else is known.
ALWAYS: Callable[[dict[FactId, object]], bool] = lambda known: True


@dataclass(frozen=True)
class Requirement:
    """One fact, and the condition under which the budget actually needs it."""

    fact: FactId
    applies: Callable[[dict[FactId, object]], bool]
    because: str


def _truthy(known: dict[FactId, object], fact: FactId) -> bool:
    return bool(known.get(fact))


REQUIREMENTS: tuple[Requirement, ...] = (
    Requirement(FactId.HOUSEHOLD_SIZE, ALWAYS,
                "the maximum allotment and the minimum benefit both key off size"),
    Requirement(FactId.STATE, ALWAYS,
                "the standard deduction, utility allowance and shelter cap are state-set"),
    Requirement(FactId.BENEFIT_MONTH, ALWAYS,
                "every constant in the budget is fiscal-year specific"),
    Requirement(FactId.EARNED_INCOME, ALWAYS,
                "earned income carries its own 20 percent deduction"),
    Requirement(FactId.UNEARNED_INCOME, ALWAYS,
                "unearned income counts in full"),
    Requirement(FactId.ELDERLY_OR_DISABLED, ALWAYS,
                "it decides both the medical deduction and whether the shelter cap applies"),
    Requirement(FactId.CHILD_SUPPORT_PAID, ALWAYS,
                "legally obligated child support is deductible"),
    Requirement(FactId.DEPENDENT_CARE, ALWAYS,
                "dependent care is deductible without a cap"),
    Requirement(FactId.HOMELESS_STATUS, ALWAYS,
                "a homeless household may take a flat standard shelter deduction instead"),
    Requirement(FactId.STATE_DETERMINED_BENEFIT, ALWAYS,
                "there is nothing to compare against without the figure on the notice"),

    # --- conditional: these depend on facts established above ---------------
    Requirement(
        FactId.MEDICAL_EXPENSES,
        lambda known: _truthy(known, FactId.ELDERLY_OR_DISABLED),
        "medical expenses above $35 are deductible only for elderly or disabled members",
    ),
    Requirement(
        FactId.SHELTER_COST,
        lambda known: not _truthy(known, FactId.HOMELESS_STATUS),
        "the excess shelter calculation is replaced by a flat deduction for homeless households",
    ),
    Requirement(
        FactId.UTILITY_ALLOWANCE,
        lambda known: not _truthy(known, FactId.HOMELESS_STATUS),
        "the utility allowance is part of total shelter cost",
    ),
)


def missing_facts(known: dict[FactId, object]) -> tuple[Requirement, ...]:
    """The facts the budget still needs, given what is established.

    ``known`` maps established facts to their values -- the values matter,
    because a conditional requirement asks about them.

    The frontier can *grow*: learning that a household has an elderly member
    adds the medical-expense requirement that did not exist a moment ago. That
    is exactly why this is a loop.
    """
    return tuple(
        requirement
        for requirement in REQUIREMENTS
        if requirement.fact not in known and requirement.applies(known)
    )


def is_closed(known: dict[FactId, object]) -> bool:
    """The loop's termination predicate. Pure, and therefore testable."""
    return not missing_facts(known)
