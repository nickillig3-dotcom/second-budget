"""The human gate pauses the loop, and the halt actually saves a model call.

Both claims are asserted against the scripted model's call counter rather than
against how the transcript reads.
"""

from __future__ import annotations

from strands import Agent

from second_budget.control.hooks import (
    INTERRUPT_NAME,
    BatchConfirmation,
    HaltWhenFrontierCloses,
)
from second_budget.facts import FactId, FactLedger, Provenance
from second_budget.models.scripted import ScriptedModel, Turn
from second_budget.nodes.elicitor import build_ledger_tools

ALL_FACTS = [
    (FactId.HOUSEHOLD_SIZE, 2), (FactId.STATE, "IL"), (FactId.BENEFIT_MONTH, "2024-06"),
    (FactId.EARNED_INCOME, 1200.0), (FactId.UNEARNED_INCOME, 0.0),
    (FactId.ELDERLY_OR_DISABLED, False), (FactId.CHILD_SUPPORT_PAID, 0.0),
    (FactId.DEPENDENT_CARE, 0.0), (FactId.HOMELESS_STATUS, False),
    (FactId.SHELTER_COST, 900.0), (FactId.UTILITY_ALLOWANCE, 0.0),
    (FactId.STATE_DETERMINED_BENEFIT, 210.0),
]


def _call(fact_id: FactId, value, provenance=Provenance.FROM_NARRATIVE):
    return ("record_fact", {
        "fact_id": fact_id.value, "value": value,
        "provenance": provenance.value, "source": "test",
    })


def _agent(turns, ledger, hooks):
    model = ScriptedModel(turns)
    return Agent(
        model=model, tools=build_ledger_tools(ledger),
        hooks=hooks, callback_handler=None,
    ), model


def test_a_whole_batch_is_confirmed_in_one_interrupt() -> None:
    ledger = FactLedger()
    gate = BatchConfirmation(ledger, navigator="M. Cook")
    agent, model = _agent(
        [Turn.tools(*[_call(f, v) for f, v in ALL_FACTS]), Turn.say("done")],
        ledger, [gate],
    )

    paused = agent("Record what the household told me.")

    assert paused.stop_reason == "interrupt"
    assert len(paused.interrupts) == 1, "twelve facts must cost one gate, not twelve"
    interrupt = paused.interrupts[0]
    # The name carries a content hash of the batch: stable across a resume of
    # the same batch, distinct between batches. A fixed name gave every round
    # the same interrupt id, so one approval silently covered the next.
    assert interrupt.name.startswith(INTERRUPT_NAME + ":")
    assert len(interrupt.reason["facts"]) == 12
    assert gate.batches_presented == 1
    # Nothing was written before a human saw it.
    assert len(ledger) == 0
    assert model.calls == 1


def test_approving_the_batch_writes_every_fact() -> None:
    ledger = FactLedger()
    gate = BatchConfirmation(ledger, navigator="M. Cook")
    agent, model = _agent(
        [Turn.tools(*[_call(f, v) for f, v in ALL_FACTS]), Turn.say("done")],
        ledger, [gate],
    )

    paused = agent("Record them.")
    agent([
        {"interruptResponse": {"interruptId": paused.interrupts[0].id,
                               "response": {"rejected": []}}}
    ])

    assert len(ledger) == 12
    assert gate.rejected == []


def test_rejecting_part_of_the_batch_reopens_the_frontier() -> None:
    """A partial rejection is not cosmetic: it changes what happens next."""
    ledger = FactLedger()
    gate = BatchConfirmation(ledger, navigator="M. Cook")
    agent, _model = _agent(
        [Turn.tools(*[_call(f, v) for f, v in ALL_FACTS]), Turn.say("done")],
        ledger, [gate],
    )

    paused = agent("Record them.")
    agent([
        {"interruptResponse": {
            "interruptId": paused.interrupts[0].id,
            "response": {"rejected": [FactId.EARNED_INCOME.value]},
        }}
    ])

    assert gate.rejected == [FactId.EARNED_INCOME.value]
    from second_budget.frontier import is_closed
    known = {f: ledger.value(f) for f in ledger.established}
    assert not is_closed(known), "a rejected fact must leave the frontier open"


def test_inferred_facts_are_flagged_for_scrutiny_in_the_payload() -> None:
    ledger = FactLedger()
    gate = BatchConfirmation(ledger, navigator="M. Cook")
    agent, _model = _agent(
        [Turn.tools(
            _call(FactId.HOUSEHOLD_SIZE, 2),
            _call(FactId.EARNED_INCOME, 1200.0, Provenance.INFERRED),
        ), Turn.say("done")],
        ledger, [gate],
    )

    paused = agent("Record them.")
    facts = paused.interrupts[0].reason["facts"]

    assert [f["needs_scrutiny"] for f in facts] == [False, True]


def test_the_halt_saves_a_model_call_and_the_counter_proves_it() -> None:
    ledger = FactLedger()
    halt = HaltWhenFrontierCloses(ledger)
    # The script offers a second turn. If the halt works, it is never consumed.
    agent, model = _agent(
        [Turn.tools(*[_call(f, v) for f, v in ALL_FACTS]),
         Turn.say("this turn must never be reached")],
        ledger, [halt],
    )

    agent("Record what the household told me.")

    assert halt.halted is True
    assert model.calls == 1, "the loop asked the model again after it had everything"
    assert len(ledger) == 12


def test_the_halt_stays_out_of_the_way_while_facts_are_still_missing() -> None:
    ledger = FactLedger()
    halt = HaltWhenFrontierCloses(ledger)
    agent, model = _agent(
        [Turn.tools(_call(FactId.HOUSEHOLD_SIZE, 2)), Turn.say("more to come")],
        ledger, [halt],
    )

    agent("Record what I have so far.")

    assert halt.halted is False
    assert model.calls == 2


def test_two_different_batches_get_two_different_gates() -> None:
    """The bug this guards against.

    ``BeforeToolsEvent`` derives the interrupt id from the NAME alone, and an id
    that already carries a response is returned rather than raised. A fixed name
    therefore lets a navigator's approval of one batch silently cover the next.
    A counter does not fix it either: the hook re-runs on resume, so the same
    batch would get a fresh id and the loop would never terminate.
    """
    from second_budget.control.hooks import _batch_key

    first = {"facts": [{"fact_id": "household.size", "value": 2,
                        "provenance": "from-narrative"}]}
    second = {"facts": [{"fact_id": "household.size", "value": 3,
                         "provenance": "from-narrative"}]}

    assert _batch_key(first) == _batch_key(dict(first))   # stable across a resume
    assert _batch_key(first) != _batch_key(second)        # distinct between batches
