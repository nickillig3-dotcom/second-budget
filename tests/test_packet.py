"""The packet cannot state a figure it did not get from somewhere.

This is the last gate before a document reaches a hearing officer. Everything
upstream can be right and this can still ship a number that came from nowhere,
so the check is on the finished artefact rather than on the pipeline.
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from second_budget.engine.budget import Household, compute
from second_budget.engine.certificate import certify
from second_budget.engine.compare import compare
from second_budget.facts import Fact, FactId, FactLedger, Provenance
from second_budget.memory.statute_store import StatuteStore
from second_budget.packet.render import Packet, PacketDoesNotVerify, render, verify

HOUSEHOLD = Household(
    size=2, earned_income=1200.0, unearned_income=0.0, standard_deduction=198.0,
    medical_expenses=25.0, shelter_expenses=900.0,
    has_elderly_or_disabled_member=True, shelter_cap=None,
    max_allotment=535, minimum_benefit=23,
)
STATED = 210


@pytest.fixture(scope="module")
def store() -> StatuteStore:
    s = StatuteStore()
    asyncio.run(s.initialize())
    return s


@pytest.fixture()
def ledger() -> FactLedger:
    led = FactLedger()
    for fact_id, value in (
        (FactId.HOUSEHOLD_SIZE, 2), (FactId.STATE, "Ohio"),
        (FactId.BENEFIT_MONTH, "2024-06"), (FactId.EARNED_INCOME, 1200.0),
        (FactId.UNEARNED_INCOME, 0.0), (FactId.ELDERLY_OR_DISABLED, True),
        (FactId.SHELTER_COST, 900.0), (FactId.UTILITY_ALLOWANCE, 0.0),
        (FactId.MEDICAL_EXPENSES, 60.0), (FactId.DEPENDENT_CARE, 0.0),
        (FactId.CHILD_SUPPORT_PAID, 0.0), (FactId.HOMELESS_STATUS, False),
        (FactId.STATE_DETERMINED_BENEFIT, float(STATED)),
    ):
        led.record(Fact(id=fact_id, value=value,
                        provenance=Provenance.FROM_NARRATIVE, source="the household"))
    return led


@pytest.fixture()
def packet(ledger, store) -> Packet:
    budget = compute(HOUSEHOLD)
    return render(
        ledger=ledger, budget=budget,
        disagreement=compare(HOUSEHOLD, agency_allotment=STATED),
        certificate=certify(budget, also={float(STATED): "the notice"}),
        store=store, navigator="M. Cook",
    )


def test_the_packet_states_both_figures_and_the_gap(packet) -> None:
    assert "$210" in packet.markdown
    assert "$473" in packet.markdown
    assert "$263 a month" in packet.markdown


def test_every_figure_records_where_it_came_from(packet) -> None:
    assert packet.figures
    for _value, origin in packet.figures:
        assert origin.split(":")[0] in {"engine", "fact", "notice", "reconciliation",
                                        "derived"}, origin


def test_every_engine_figure_is_in_the_certificate(packet, ledger) -> None:
    budget = compute(HOUSEHOLD)
    certificate = certify(budget, also={float(STATED): "the notice"})
    for value, origin in packet.figures:
        if origin.startswith("engine:"):
            assert certificate.permits(value), f"{origin} = {value} is not certified"


def test_every_quotation_is_a_literal_span_of_the_section_it_cites(packet, store) -> None:
    assert packet.quotations, "the packet quotes no regulation at all"
    for citation, span in packet.quotations:
        assert span in store.section_text(citation)


def test_a_figure_from_nowhere_is_refused(packet, store) -> None:
    """The failure this whole file exists for."""
    budget = compute(HOUSEHOLD)
    certificate = certify(budget, also={float(STATED): "the notice"})
    tampered = dataclasses.replace(
        packet, figures=packet.figures + ((9999.0, "engine:invented"),)
    )
    with pytest.raises(PacketDoesNotVerify) as refusal:
        verify(tampered, certificate=certificate, store=store)
    assert "9999" in str(refusal.value)


def test_a_quotation_that_drifted_from_the_statute_is_refused(packet, store) -> None:
    budget = compute(HOUSEHOLD)
    certificate = certify(budget, also={float(STATED): "the notice"})
    tampered = dataclasses.replace(
        packet,
        quotations=packet.quotations + (("7 CFR 273.9", "Thirty percent of gross earned income"),),
    )
    with pytest.raises(PacketDoesNotVerify):
        verify(tampered, certificate=certificate, store=store)


def test_a_household_size_is_not_rendered_as_currency(packet) -> None:
    assert "| household.size | 2 |" in packet.markdown
    assert "| household.size | $2 |" not in packet.markdown


def test_the_packet_says_plainly_that_it_is_not_an_accusation(packet) -> None:
    """The claim is 'this would have to be true', not 'the agency was wrong'."""
    assert "not an allegation" in packet.markdown
    assert "may hold information the household did not mention" in packet.markdown


def test_the_packet_says_it_is_not_legal_advice(packet) -> None:
    assert "not legal advice" in packet.markdown


def test_an_agreeing_case_produces_no_reconciliation_section(ledger, store) -> None:
    budget = compute(HOUSEHOLD)
    agreeing = render(
        ledger=ledger, budget=budget,
        disagreement=compare(HOUSEHOLD, agency_allotment=budget.allotment),
        certificate=certify(budget), store=store,
    )
    assert "There is nothing to dispute on the arithmetic." in agreeing.markdown
    assert "What would have to be true" not in agreeing.markdown
