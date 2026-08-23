"""The elicitation loop converges, and it converges because code said so.

Every test here runs the real Strands graph on a scripted model: real edge
conditions, real node scheduling, real cycle. No credentials, no network.
"""

from __future__ import annotations

import pytest
from strands import Agent
from strands.multiagent import Status

from second_budget.facts import FactId, FactLedger, Provenance
from second_budget.frontier import is_closed, missing_facts
from second_budget.models.scripted import ScriptedModel, Turn
from second_budget.nodes.elicitor import build_elicitor
from second_budget.nodes.graph import (
    GraphDidNotConverge,
    build_graph,
    frontier_closed,
    frontier_still_open,
    run,
)
from second_budget.nodes.solver_node import LEDGER_KEY, RESULT_KEY, BudgetSolver

# No constants are passed in. The engine resolves them from the household's own
# recorded state, which is the only way a wrong jurisdiction cannot be supplied
# alongside a right one.


def _fact(fact_id: FactId, value, provenance=Provenance.FROM_NARRATIVE):
    return ("record_fact", {
        "fact_id": fact_id.value,
        "value": value,
        "provenance": provenance.value,
        "source": "test fixture",
    })


#: Deliberately dribbled out over three rounds, and the elderly fact arrives in
#: round two -- which *adds* the medical requirement mid-loop. A fixed form
#: cannot express that; a cycle can.
ROUND_ONE = [
    _fact(FactId.HOUSEHOLD_SIZE, 2),
    _fact(FactId.STATE, "Ohio"),
    _fact(FactId.BENEFIT_MONTH, "2024-06"),
    _fact(FactId.EARNED_INCOME, 1200.0),
]
ROUND_TWO = [
    _fact(FactId.UNEARNED_INCOME, 0.0),
    _fact(FactId.ELDERLY_OR_DISABLED, True),
    _fact(FactId.CHILD_SUPPORT_PAID, 0.0),
    _fact(FactId.DEPENDENT_CARE, 0.0),
]
ROUND_THREE = [
    _fact(FactId.HOMELESS_STATUS, False),
    _fact(FactId.SHELTER_COST, 900.0),
    _fact(FactId.UTILITY_ALLOWANCE, 0.0),
    _fact(FactId.STATE_DETERMINED_BENEFIT, 210.0),
    _fact(FactId.MEDICAL_EXPENSES, 60.0),
]


def _converging_graph():
    ledger = FactLedger()
    elicitor_model = ScriptedModel([
        Turn.tools(*ROUND_ONE), Turn.say("recorded what I had"),
        Turn.tools(*ROUND_TWO), Turn.say("recorded what I had"),
        Turn.tools(*ROUND_THREE), Turn.say("that is everything"),
    ])
    drafter_model = ScriptedModel([Turn.say("Fair hearing request drafted.")])
    engine = BudgetSolver()
    graph = build_graph(
        elicitor=build_elicitor(elicitor_model, ledger),
        drafter=Agent(model=drafter_model, callback_handler=None),
        engine=engine,
    )
    state = {LEDGER_KEY: ledger}
    return graph, state, ledger, engine, elicitor_model


def test_the_loop_cycles_until_the_engine_says_it_has_everything() -> None:
    graph, state, ledger, engine, elicitor_model = _converging_graph()

    result = run(graph, "Re-derive this household's SNAP allotment.", state)

    order = [node.node_id for node in result.execution_order]
    assert order == [
        "elicitor", "engine",
        "elicitor", "engine",
        "elicitor", "engine",
        "drafter",
    ], order
    assert engine.runs == 3
    assert result.status is Status.COMPLETED
    assert is_closed({f: ledger.value(f) for f in ledger.established})


def test_the_engine_produced_a_budget_and_the_drafter_ran_after_it() -> None:
    graph, state, _ledger, _engine, _model = _converging_graph()
    run(graph, "Re-derive it.", state)

    budget = state[RESULT_KEY]
    assert budget.allotment > 0
    # 2-person household, elderly member -> the shelter cap must not have applied.
    assert "uncapped" in budget.stage("excess_shelter_deduction").note


def test_the_frontier_grows_when_a_fact_unlocks_another() -> None:
    """Learning about an elderly member adds a requirement that did not exist."""
    without = {FactId.ELDERLY_OR_DISABLED: False}
    with_elderly = {FactId.ELDERLY_OR_DISABLED: True}

    assert FactId.MEDICAL_EXPENSES not in {r.fact for r in missing_facts(without)}
    assert FactId.MEDICAL_EXPENSES in {r.fact for r in missing_facts(with_elderly)}


def test_a_homeless_household_is_never_asked_for_shelter_costs() -> None:
    homeless = {FactId.HOMELESS_STATUS: True}
    asked = {r.fact for r in missing_facts(homeless)}
    assert FactId.SHELTER_COST not in asked
    assert FactId.UTILITY_ALLOWANCE not in asked


def test_a_loop_that_never_converges_is_reported_not_silently_accepted() -> None:
    """A limit breach leaves status FAILED with failed_nodes == 0.

    ``if result.failed_nodes:`` would miss it entirely, and the caller would
    treat a runaway as a finished case. ``run`` checks status instead.
    """
    ledger = FactLedger()
    # Records the same single fact forever: the frontier can never close.
    turns = []
    for _ in range(20):
        turns += [Turn.tools(_fact(FactId.HOUSEHOLD_SIZE, 2)), Turn.say("still going")]
    graph = build_graph(
        elicitor=build_elicitor(ScriptedModel(turns), ledger),
        drafter=Agent(model=ScriptedModel([Turn.say("never reached")]), callback_handler=None),
    )
    with pytest.raises(GraphDidNotConverge) as caught:
        run(graph, "go", {LEDGER_KEY: ledger})
    assert "never closed" in str(caught.value)


def test_the_edge_conditions_are_pure_and_complementary() -> None:
    """The SDK evaluates a condition twice per accepted traversal.

    Anything with a side effect would fire twice. And the pair must partition:
    an ambiguous frontier would either stall the graph or run both branches.
    """
    ledger = FactLedger()
    state = {LEDGER_KEY: ledger}

    for _ in range(3):
        assert frontier_still_open(None, invocation_state=state) is True
        assert frontier_closed(None, invocation_state=state) is False
    assert len(ledger) == 0  # nothing was mutated by asking

    for fact_id, value in (
        (FactId.HOUSEHOLD_SIZE, 2), (FactId.STATE, "Ohio"), (FactId.BENEFIT_MONTH, "2024-06"),
        (FactId.EARNED_INCOME, 1200.0), (FactId.UNEARNED_INCOME, 0.0),
        (FactId.ELDERLY_OR_DISABLED, False), (FactId.CHILD_SUPPORT_PAID, 0.0),
        (FactId.DEPENDENT_CARE, 0.0), (FactId.HOMELESS_STATUS, False),
        (FactId.SHELTER_COST, 900.0), (FactId.UTILITY_ALLOWANCE, 0.0),
        (FactId.STATE_DETERMINED_BENEFIT, 210.0),
    ):
        from second_budget.facts import Fact
        ledger.record(Fact(id=fact_id, value=value, provenance=Provenance.FROM_NARRATIVE))

    assert frontier_still_open(None, invocation_state=state) is False
    assert frontier_closed(None, invocation_state=state) is True


def test_the_condition_parameter_is_spelled_the_way_the_sdk_dispatches_on() -> None:
    """Renaming this parameter silently changes the calling convention.

    ``multiagent/graph.py:97`` inspects the parameter *name*. A condition
    spelled ``ctx=None`` is called with the legacy one-argument form, gets its
    default forever, and turns a convergent loop into a runaway with no error.
    """
    import inspect

    for condition in (frontier_still_open, frontier_closed):
        assert "invocation_state" in inspect.signature(condition).parameters
