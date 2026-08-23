"""The model cannot state a number the engine did not compute.

The claim these tests defend is a legal one, not an aesthetic one: a fabricated
figure in a fair-hearing request loses the hearing and spends the household's one
appeal. So it is enforced by a gate with a decidable predicate -- set membership
over an engine certificate -- and the tests assert on what reached the document,
not on what the model was asked to do.
"""

from __future__ import annotations

import pytest
from strands import Agent, tool

from second_budget.control.interventions import InferredFactGate, NumbersGate
from second_budget.engine.budget import Household, compute
from second_budget.engine.certificate import certify, money_tokens
from second_budget.facts import Provenance
from second_budget.models.scripted import ScriptedModel, Turn

HOUSEHOLD = Household(
    size=2, earned_income=1200.0, standard_deduction=204.0,
    shelter_expenses=900.0, shelter_cap=672.0,
    max_allotment=535, minimum_benefit=23,
)


@pytest.fixture(scope="module")
def certificate():
    return certify(compute(HOUSEHOLD), also={210.0: "the benefit stated on the notice"})


def _drafting_agent(turns, gate):
    written: list[str] = []

    @tool
    def write_finding(text: str) -> str:
        """Write one line of the fair-hearing request."""
        written.append(text)
        return "written"

    model = ScriptedModel(turns)
    agent = Agent(
        model=model, tools=[write_finding],
        interventions=[gate], callback_handler=None,
    )
    return agent, model, written


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("we computed $1,752.00", (1752.0,)),
        ("$1,234.56 and 291", (1234.56, 291.0)),
        ("no figures here", ()),
        ("the allotment is 464", (464.0,)),
    ],
)
def test_money_is_detected_in_the_shapes_people_actually_write(text, expected) -> None:
    assert money_tokens(text) == expected


def test_an_invented_figure_never_reaches_the_document(certificate) -> None:
    allotment = compute(HOUSEHOLD).allotment
    gate = NumbersGate(certificate, guarded_tools=frozenset({"write_finding"}))
    agent, model, written = _drafting_agent(
        [
            Turn.tool("write_finding", {"text": "Your net income is $1,800."}),
            Turn.tool("write_finding", {"text": f"The allotment is ${allotment}."}),
            Turn.say("done"),
        ],
        gate,
    )

    agent("Draft the request.")

    assert written == [f"The allotment is ${allotment}."]
    assert gate.denials == [("write_finding", (1800.0,))]
    # The model was told, retried, and succeeded -- three calls, not a crash.
    assert model.calls == 3


def test_the_denial_tells_the_model_what_it_may_use(certificate) -> None:
    """The reason string is the entire steering signal.

    Strands imposes no retry cap after a Deny, so a reason that merely complains
    produces an unbounded loop. This one is an instruction.
    """
    gate = NumbersGate(certificate, guarded_tools=frozenset({"write_finding"}))
    agent, _model, _written = _drafting_agent(
        [Turn.tool("write_finding", {"text": "$9,999"}), Turn.say("giving up")],
        gate,
    )
    agent("Draft it.")

    tool_results = [
        block["toolResult"]
        for message in agent.messages
        for block in message.get("content", [])
        if isinstance(block, dict) and "toolResult" in block
    ]
    text = tool_results[0]["content"][0]["text"]
    assert tool_results[0]["status"] == "error"
    assert "9999 was not computed by the engine" in text
    assert "Use one of these values" in text


def test_the_figure_on_the_notice_is_quotable_even_though_we_did_not_compute_it(
    certificate,
) -> None:
    """A packet has to be able to say what the agency decided."""
    gate = NumbersGate(certificate, guarded_tools=frozenset({"write_finding"}))
    agent, _model, written = _drafting_agent(
        [Turn.tool("write_finding", {"text": "The notice states $210."}), Turn.say("done")],
        gate,
    )
    agent("Draft it.")

    assert written == ["The notice states $210."]
    assert gate.denials == []


def test_an_unguarded_tool_is_left_alone(certificate) -> None:
    gate = NumbersGate(certificate, guarded_tools=frozenset({"some_other_tool"}))
    agent, _model, written = _drafting_agent(
        [Turn.tool("write_finding", {"text": "$1,800"}), Turn.say("done")], gate
    )
    agent("Draft it.")

    assert written == ["$1,800"]


def test_the_gate_fails_closed_when_it_cannot_do_its_job(certificate) -> None:
    """on_error is 'deny', not the SDK default.

    'throw' would be fail-closed but would kill a case a navigator is in the
    middle of. 'proceed' is the one fail-open value and would write the figure.
    """
    gate = NumbersGate(certificate, guarded_tools=frozenset({"write_finding"}))
    assert gate.on_error == "deny"


def test_an_inferred_fact_pauses_for_a_human() -> None:
    gate = InferredFactGate()

    @tool
    def record_fact(fact_id: str, value: float, provenance: str, source: str = "") -> str:
        """Record one fact."""
        return "recorded"

    model = ScriptedModel([
        Turn.tool("record_fact", {
            "fact_id": "income.earned", "value": 1200.0,
            "provenance": Provenance.INFERRED.value, "source": "worked it out",
        }),
        Turn.say("done"),
    ])
    agent = Agent(model=model, tools=[record_fact], interventions=[gate],
                  callback_handler=None)

    paused = agent("Record it.")

    assert paused.stop_reason == "interrupt"
    assert gate.confirmations_requested == ["income.earned"]


def test_a_fact_that_was_read_rather_than_inferred_does_not_pause() -> None:
    gate = InferredFactGate()

    @tool
    def record_fact(fact_id: str, value: float, provenance: str, source: str = "") -> str:
        """Record one fact."""
        return "recorded"

    model = ScriptedModel([
        Turn.tool("record_fact", {
            "fact_id": "income.earned", "value": 1200.0,
            "provenance": Provenance.FROM_NOTICE.value, "source": "line 4 of the notice",
        }),
        Turn.say("done"),
    ])
    agent = Agent(model=model, tools=[record_fact], interventions=[gate],
                  callback_handler=None)

    result = agent("Record it.")

    assert result.stop_reason == "end_turn"
    assert gate.confirmations_requested == []
