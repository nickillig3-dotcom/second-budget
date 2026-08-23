"""The scripted provider must drive a real agent loop, not bypass it.

These tests are the foundation the rest of the suite stands on: if the scripted
model did not exercise the genuine Strands event loop, every later assertion
about interventions, interrupts and graph cycles would be assertions about a
mock.
"""

from __future__ import annotations

import pytest
from strands import Agent, tool

from second_budget.models.scripted import ScriptExhausted, ScriptedModel, Turn


@tool
def record_fact(fact_id: str, value: float) -> str:
    """Record one confirmed fact about the household."""
    return f"recorded {fact_id}={value}"


def _agent(turns: list[Turn]) -> tuple[Agent, ScriptedModel]:
    model = ScriptedModel(turns)
    return Agent(model=model, tools=[record_fact], callback_handler=None), model


def test_tool_calls_reach_the_tool_and_the_loop_terminates() -> None:
    agent, model = _agent(
        [
            Turn.tool("record_fact", {"fact_id": "shelter.rent", "value": 900}),
            Turn.say("I have what I need."),
        ]
    )
    result = agent("Start elicitation.")

    assert "I have what I need." in str(result)
    # Two model calls: one producing the tool use, one after the tool result.
    assert model.calls == 2


def test_multiple_tool_calls_in_one_turn_all_execute() -> None:
    agent, model = _agent(
        [
            Turn.tools(
                ("record_fact", {"fact_id": "income.earned", "value": 1200}),
                ("record_fact", {"fact_id": "hh.size", "value": 2}),
            ),
            Turn.say("done"),
        ]
    )
    agent("go")

    tool_results = [
        block["toolResult"]
        for message in agent.messages
        for block in message.get("content", [])
        if isinstance(block, dict) and "toolResult" in block
    ]
    assert len(tool_results) == 2


def test_the_model_actually_sees_the_conversation() -> None:
    """Guards against a provider that ignores its input and replays blindly."""
    agent, model = _agent(
        [
            Turn.tool("record_fact", {"fact_id": "shelter.rent", "value": 900}),
            Turn.say("done"),
        ]
    )
    agent("the household pays 900 in rent")

    first_call = model.seen_messages[0]
    assert any(
        "900" in block.get("text", "")
        for message in first_call
        for block in message.get("content", [])
        if isinstance(block, dict)
    )
    # The second call must include the tool result, or the loop is not looping.
    assert len(model.seen_messages[1]) > len(first_call)


def test_running_off_the_end_of_the_script_is_loud() -> None:
    """A runaway loop must fail the test, never silently produce a default turn.

    Note the wrapping: the event loop catches provider exceptions and re-raises
    them as ``EventLoopException`` (``event_loop.py:404``), so a test that waits
    for the bare ``ScriptExhausted`` never sees it. The original is preserved as
    ``__cause__``, which is what we assert on.
    """
    from strands.types.exceptions import EventLoopException

    agent, _ = _agent([Turn.tool("record_fact", {"fact_id": "x", "value": 1})])
    with pytest.raises(EventLoopException) as caught:
        agent("go")

    assert isinstance(caught.value.__cause__, ScriptExhausted)
    assert "the script has 1" in str(caught.value)


def test_config_round_trips() -> None:
    model = ScriptedModel([], model_id="scripted-a")
    assert model.get_config()["model_id"] == "scripted-a"
    model.update_config(model_id="scripted-b", temperature=0.0)
    assert model.get_config() == {"model_id": "scripted-b", "temperature": 0.0}


def test_structured_output_validates_the_scripted_payload() -> None:
    from pydantic import BaseModel

    class Fact(BaseModel):
        fact_id: str
        value: float

    import asyncio

    model = ScriptedModel([Turn.tool("payload", {"fact_id": "hh.size", "value": 2})])

    async def collect() -> Fact:
        async for event in model.structured_output(Fact, [], None):
            return event["output"]
        raise AssertionError("no structured output produced")

    fact = asyncio.run(collect())
    assert fact.fact_id == "hh.size"
    assert fact.value == 2.0
