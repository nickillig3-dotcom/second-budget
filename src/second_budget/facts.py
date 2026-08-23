"""The fact ledger: what is known about a household, and how it came to be known.

Every number that reaches a household's appeal is traceable to one entry here,
and every entry records **where it came from**. That is the difference between a
filing an advocate can defend and a filing that loses a hearing.

Three provenance classes, and they are not decoration:

``FROM_NOTICE``
    Read off the notice of action the household was sent. The strongest kind:
    the agency asserted it in writing.
``FROM_NARRATIVE``
    Stated by the household to the navigator. Contestable, but first-hand.
``INFERRED``
    The model concluded it from something else. **These never enter a budget
    without a human confirming them**, because an inferred fact is the model's
    opinion wearing the costume of a datum.

The distinction is enforced structurally rather than promised: an inferred fact
that has not been confirmed is not merely flagged, it is refused by the ledger.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace


class Provenance(enum.Enum):
    FROM_NOTICE = "from-notice"
    FROM_NARRATIVE = "from-narrative"
    INFERRED = "inferred"

    @property
    def needs_confirmation(self) -> bool:
        """Only the model's own conclusions require a human to sign them off."""
        return self is Provenance.INFERRED


class FactId(str, enum.Enum):
    """Every fact the budget can consume. A closed set on purpose.

    An open string namespace would let the model invent a fact name that no
    stage reads, and the frontier would never close.
    """

    HOUSEHOLD_SIZE = "household.size"
    EARNED_INCOME = "income.earned"
    UNEARNED_INCOME = "income.unearned"
    ELDERLY_OR_DISABLED = "household.elderly_or_disabled"
    DEPENDENT_CARE = "expenses.dependent_care"
    MEDICAL_EXPENSES = "expenses.medical"
    CHILD_SUPPORT_PAID = "expenses.child_support"
    SHELTER_COST = "shelter.cost"
    UTILITY_ALLOWANCE = "shelter.utility_allowance"
    HOMELESS_STATUS = "household.homeless"
    STATE = "household.state"
    BENEFIT_MONTH = "case.benefit_month"
    STATE_DETERMINED_BENEFIT = "case.state_benefit"


@dataclass(frozen=True)
class Fact:
    id: FactId
    value: float | bool | str
    provenance: Provenance
    source: str = ""                 # the notice line, or the words the household used
    confirmed_by: str | None = None  # the navigator who signed it off
    confirmed_at: str | None = None

    @property
    def is_usable(self) -> bool:
        """May this fact enter a budget?"""
        return not self.provenance.needs_confirmation or self.confirmed_by is not None

    def confirmed(self, by: str, at: str) -> "Fact":
        return replace(self, confirmed_by=by, confirmed_at=at)


class UnconfirmedFact(ValueError):
    """An inferred fact was asked to enter a budget without a human signature."""


@dataclass
class FactLedger:
    """The authoritative record of what is established.

    Deliberately mutable and deliberately *not* the conversation. The elicitor
    re-reads this every round rather than trusting its own previous turns --
    which is why the graph resets that node on revisit. A model that is allowed
    to remember what it decided becomes the authority on what is true.
    """

    entries: dict[FactId, Fact] = field(default_factory=dict)

    def record(self, fact: Fact) -> None:
        self.entries[fact.id] = fact

    def confirm(self, fact_id: FactId, by: str, at: str) -> None:
        self.entries[fact_id] = self.entries[fact_id].confirmed(by, at)

    def get(self, fact_id: FactId) -> Fact | None:
        return self.entries.get(fact_id)

    def value(self, fact_id: FactId) -> float | bool | str:
        """Read a fact's value, refusing anything a human has not signed off."""
        fact = self.entries[fact_id]
        if not fact.is_usable:
            raise UnconfirmedFact(
                f"{fact_id.value} was inferred by the model and has not been "
                f"confirmed by a navigator; it may not enter a budget"
            )
        return fact.value

    @property
    def established(self) -> frozenset[FactId]:
        """Facts that exist and are allowed to be used."""
        return frozenset(k for k, v in self.entries.items() if v.is_usable)

    @property
    def awaiting_confirmation(self) -> tuple[Fact, ...]:
        return tuple(f for f in self.entries.values() if not f.is_usable)

    def __len__(self) -> int:
        return len(self.entries)
