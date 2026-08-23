"""Render the fair-hearing request, with every claim traceable to its evidence.

This is the artefact a household actually files. Two properties are enforced by
construction rather than asked for:

**Every monetary figure records where it came from, as it is written.** Not
scraped back out of the finished prose afterwards -- a regex over the final text
cannot tell the $263 in a disagreement from the 273.9 in a citation, and a
verifier that cannot tell them apart is not a verifier. The renderer knows the
origin of each figure at the moment it emits it, so that is where it is recorded.

**Every quotation is verbatim.** ``verify`` re-checks each quoted span against
the statute index. If the packet has drifted from its evidence, the render fails
rather than producing a document that reads well and cannot be defended.

The tone is deliberately flat. A fair-hearing request is read by a hearing
officer with a stack of them; it should state what is disputed, what the
household says, and what the regulation says, and stop.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..engine.budget import BudgetResult
from ..engine.certificate import Certificate
from ..engine.compare import Disagreement
from ..facts import FactId, FactLedger

#: Facts worth restating in the packet, in the order a reader wants them.
NARRATIVE_ORDER = (
    FactId.HOUSEHOLD_SIZE,
    FactId.STATE,
    FactId.BENEFIT_MONTH,
    FactId.EARNED_INCOME,
    FactId.UNEARNED_INCOME,
    FactId.ELDERLY_OR_DISABLED,
    FactId.SHELTER_COST,
    FactId.UTILITY_ALLOWANCE,
    FactId.MEDICAL_EXPENSES,
    FactId.DEPENDENT_CARE,
    FactId.CHILD_SUPPORT_PAID,
    FactId.HOMELESS_STATUS,
    FactId.STATE_DETERMINED_BENEFIT,
)

#: Which regulation to quote alongside which budget stage.
QUOTE_FOR_STAGE = {
    "earned_income_deduction": ("7 CFR 273.9", "Twenty percent of gross earned income"),
    "medical_deduction": ("7 CFR 273.9", "in excess of $35 per month"),
    "excess_shelter_deduction": (
        "7 CFR 273.9",
        "in excess of 50 percent of the household",
    ),
    "allotment": (
        "7 CFR 273.10",
        "round the 30 percent of net income up to the nearest higher dollar",
    ),
}


#: Numeric facts that are counts, not amounts. Rendering a household of two
#: people as "$2" is small, but a document that cannot tell a count from a
#: currency has no business asserting either.
NOT_MONEY = frozenset({FactId.HOUSEHOLD_SIZE})


class PacketDoesNotVerify(AssertionError):
    """The rendered document no longer follows from its evidence."""


@dataclass(frozen=True)
class Packet:
    markdown: str
    #: Every monetary figure the document states, paired with its origin.
    figures: tuple[tuple[float, str], ...]
    quotations: tuple[tuple[str, str], ...]


class _Figures:
    """Collects every monetary figure the document states, with its origin."""

    def __init__(self) -> None:
        self.seen: list[tuple[float, str]] = []

    def __call__(self, value: float, origin: str) -> str:
        assert origin, "every figure in the packet must record where it came from"
        self.seen.append((round(float(value), 2), origin))
        return f"${value:,.0f}" if float(value).is_integer() else f"${value:,.2f}"


def _fact_line(ledger: FactLedger, fact_id: FactId, money: _Figures) -> str | None:
    fact = ledger.get(fact_id)
    if fact is None:
        return None
    if isinstance(fact.value, bool):
        shown = "yes" if fact.value else "no"
    elif fact_id in NOT_MONEY:
        shown = f"{int(fact.value)}"
    elif isinstance(fact.value, (int, float)):
        shown = money(float(fact.value), f"fact:{fact_id.value}")
    else:
        shown = str(fact.value)
    source = f" -- {fact.source}" if fact.source else ""
    return f"| {fact_id.value} | {shown} | {fact.provenance.value}{source} |"


def render(
    *,
    ledger: FactLedger,
    budget: BudgetResult,
    disagreement: Disagreement,
    certificate: Certificate,
    store,
    navigator: str = "(navigator name)",
) -> Packet:
    """Build the packet. Raises if it does not verify against its own evidence."""
    quotations: list[tuple[str, str]] = []
    money = _Figures()

    def quote(stage_name: str) -> str:
        entry = QUOTE_FOR_STAGE.get(stage_name)
        if entry is None:
            return ""
        citation, span = entry
        source = store.section_text(citation)
        if source is None or span not in source:
            raise PacketDoesNotVerify(
                f"the span quoted for {stage_name} is not in {citation}; "
                f"the statute index is stale or the span was edited"
            )
        quotations.append((citation, span))
        return f'\n\n  > "...{span}..." -- {citation}'

    lines: list[str] = []
    add = lines.append

    add("# Request for a fair hearing")
    add("")
    add("## What is disputed")
    add("")
    add(
        f"The notice of action states a monthly SNAP allotment of "
        f"**{money(disagreement.stated_by_agency, 'notice')}**. An independent "
        f"recomputation from the household's own circumstances produces "
        f"**{money(disagreement.derived, 'engine:allotment')}**."
    )
    add("")
    if disagreement.agrees:
        add("The two figures agree. There is nothing to dispute on the arithmetic.")
    else:
        direction = "understates" if disagreement.household_is_owed else "overstates"
        add(
            f"The notice {direction} the allotment by "
            f"**{money(abs(disagreement.gap), 'derived:gap')} a month**."
        )

    add("")
    add("## The facts this rests on")
    add("")
    add("| fact | value | where it came from |")
    add("| --- | --- | --- |")
    for fact_id in NARRATIVE_ORDER:
        line = _fact_line(ledger, fact_id, money)
        if line:
            add(line)
    add("")
    add(
        "Facts marked *inferred* were concluded by software rather than stated by "
        "the household, and were confirmed by the navigator before use."
    )

    add("")
    add("## The calculation, stage by stage")
    add("")
    for stage in budget.stages:
        if stage.name in ("gross_income", "adjusted_income"):
            continue
        note = f" ({stage.note})" if stage.note else ""
        amount = money(stage.value, f"engine:{stage.name}")
        add(f"- **{stage.name.replace('_', ' ')}**: {amount} -- {stage.cfr}"
            f"{note}{quote(stage.name)}")

    if not disagreement.agrees:
        add("")
        add("## What would have to be true for the notice to be correct")
        add("")
        if disagreement.reconciliations:
            add(
                "The budget is monotone in each of these inputs, so each line below "
                "is the single value of that input which would produce the figure on "
                "the notice. Each was checked by recomputing the budget with it."
            )
            add("")
            for line in disagreement.reconciliations:
                required = money(line.required, f"reconciliation:{line.field}")
                stated = money(line.stated, f"fact:{line.field}")
                gap = money(abs(line.difference), f"reconciliation:{line.field}")
                add(f"- {line.label}: **{required}** rather than {stated} -- a "
                    f"difference of {gap} {line.direction} ({line.cfr})")
            add("")
            add(
                "Inputs that cannot account for the difference at any permitted value "
                "are omitted rather than listed with an impossible figure."
            )
        else:
            add(
                "No single input, at any permitted value, produces the figure on the "
                "notice. The difference cannot be explained by one changed fact."
            )
        add("")
        add(
            "This is not an allegation. The agency may hold information the "
            "household did not mention, and the household may be misremembering. "
            "It states what would have to be true, so that it can be checked."
        )

    add("")
    add("## Requested")
    add("")
    add(
        "The household requests a fair hearing and asks that the budget be "
        "recomputed, with the stages above examined."
    )
    add("")
    add(f"Prepared with the household by: {navigator}")
    add("")
    add(
        "*This document is a draft prepared for review by a benefits navigator or "
        "advocate. It is not legal advice.*"
    )

    packet = Packet(
        markdown="\n".join(lines),
        figures=tuple(money.seen),
        quotations=tuple(quotations),
    )
    verify(packet, certificate=certificate, store=store)
    return packet


def verify(packet: Packet, *, certificate: Certificate, store) -> None:
    """Re-check the finished document against its own evidence.

    Every monetary figure is one of three things, and the document says which as
    it writes them:

    ``engine:*``
        A stage the engine computed. Must be in the certificate.
    ``fact:*``
        A value the household or the notice supplied. Allowed by definition -- it
        is not ours to invent, and not ours to correct either.
    ``reconciliation:*``
        A value solved for by the comparison. Allowed, and separately verified by
        ``compare`` re-running the whole budget with it.
    ``derived:*``
        Arithmetic over figures already in this list -- the gap is the notice
        minus the engine's allotment, both of which are checked above. It is not
        a stage, so it is not in the certificate, and giving it a class of its
        own is better than widening the engine rule to let it through.

    A figure with no origin at all appeared from nowhere, and that is the failure
    this exists to catch.
    """
    unsourced = [
        (value, origin)
        for value, origin in packet.figures
        if origin.startswith("engine:") and not certificate.permits(value)
    ]
    if unsourced:
        raise PacketDoesNotVerify(
            f"the packet states engine figures the certificate does not carry: {unsourced}"
        )
    if any(not origin for _value, origin in packet.figures):
        raise PacketDoesNotVerify("the packet states a figure with no recorded origin")

    for citation, span in packet.quotations:
        source = store.section_text(citation)
        if source is None or span not in source:
            raise PacketDoesNotVerify(
                f"the packet quotes {span!r} as {citation}, which does not contain it"
            )
