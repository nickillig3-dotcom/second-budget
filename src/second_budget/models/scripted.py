"""A Strands ``Model`` provider that returns a script instead of calling anything.

Why this exists, and why it is the first thing built rather than a test helper:

The claim this project is judged on is "the decisions in this system are
provable". An agent whose behaviour can only be observed by paying a model
provider is not provable -- it is sampled. A scripted provider makes the entire
agent loop deterministic: graph topology, edge conditions, every intervention
Deny, every interrupt round, and the exact number of model calls are all
assertable in a unit test, with no AWS account, no network, and no spend.

It is a real ``Model`` implementation, not a mock. The agent loop cannot tell the
difference: it emits the same ``StreamEvent`` sequence Bedrock does --
``messageStart`` then ``contentBlockStart`` / ``contentBlockDelta`` /
``contentBlockStop`` per block, then ``messageStop`` and ``metadata``. That is
the point. A mock that bypasses the loop proves nothing about the loop.

    turns = [
        Turn.tool("record_fact", {"fact_id": "shelter.rent", "value": 900}),
        Turn.say("I have what I need."),
    ]
    agent = Agent(model=ScriptedModel(turns), tools=[record_fact])

``ScriptedModel.calls`` counts model invocations, which is how a test asserts
that ``AfterToolsEvent.end_turn`` really did halt the loop rather than merely
appear to.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel
from strands.models.model import Model
from strands.types.content import Messages
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolChoice, ToolSpec

T = TypeVar("T", bound=BaseModel)


class ScriptExhausted(RuntimeError):
    """The agent asked for one more turn than the script provides.

    Deliberately an error rather than a benign default. A silent fallback turn
    would let a runaway loop look like a passing test, which is exactly the
    failure a scripted provider exists to catch.
    """


@dataclass
class Turn:
    """One model response: free text, or one or more tool calls."""

    text: str | None = None
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    stop_reason: str | None = None

    @classmethod
    def say(cls, text: str) -> "Turn":
        return cls(text=text, stop_reason="end_turn")

    @classmethod
    def tool(cls, name: str, payload: dict[str, Any]) -> "Turn":
        return cls(tool_calls=[(name, payload)], stop_reason="tool_use")

    @classmethod
    def tools(cls, *calls: tuple[str, dict[str, Any]]) -> "Turn":
        return cls(tool_calls=list(calls), stop_reason="tool_use")


class ScriptedModel(Model):
    """Deterministic ``Model``: replays ``turns`` in order, counting calls."""

    def __init__(self, turns: list[Turn], *, model_id: str = "scripted") -> None:
        self._turns = list(turns)
        self._config: dict[str, Any] = {"model_id": model_id}
        self.calls = 0
        self.seen_messages: list[Messages] = []

    # -- Model interface --------------------------------------------------

    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    def get_config(self) -> dict[str, Any]:
        return dict(self._config)

    async def structured_output(
        self,
        output_model: type[T],
        prompt: Messages,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, T | Any], None]:
        """Validate the next scripted payload into ``output_model``.

        Structured output is scripted the same way everything else is: the next
        turn must carry exactly one tool call whose payload validates.
        """
        turn = self._next()
        if len(turn.tool_calls) != 1:
            raise ScriptExhausted(
                "structured_output needs a turn with exactly one tool call "
                "carrying the payload"
            )
        yield {"output": output_model.model_validate(turn.tool_calls[0][1])}

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        *,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        self.seen_messages.append(messages)
        turn = self._next()

        yield {"messageStart": {"role": "assistant"}}

        index = 0
        if turn.text:
            yield {"contentBlockStart": {"contentBlockIndex": index, "start": {}}}
            yield {
                "contentBlockDelta": {
                    "contentBlockIndex": index,
                    "delta": {"text": turn.text},
                }
            }
            yield {"contentBlockStop": {"contentBlockIndex": index}}
            index += 1

        for n, (name, payload) in enumerate(turn.tool_calls):
            yield {
                "contentBlockStart": {
                    "contentBlockIndex": index,
                    "start": {
                        "toolUse": {
                            "name": name,
                            "toolUseId": f"scripted-{self.calls}-{n}",
                        }
                    },
                }
            }
            yield {
                "contentBlockDelta": {
                    "contentBlockIndex": index,
                    "delta": {"toolUse": {"input": json.dumps(payload)}},
                }
            }
            yield {"contentBlockStop": {"contentBlockIndex": index}}
            index += 1

        stop = turn.stop_reason or ("tool_use" if turn.tool_calls else "end_turn")
        yield {"messageStop": {"stopReason": stop}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
                "metrics": {"latencyMs": 0},
            }
        }

    # -- internals --------------------------------------------------------

    def _next(self) -> Turn:
        if self.calls >= len(self._turns):
            raise ScriptExhausted(
                f"the agent asked for turn {self.calls + 1} but the script has "
                f"{len(self._turns)}. Either the loop is not terminating, or "
                f"the script is short."
            )
        turn = self._turns[self.calls]
        self.calls += 1
        return turn
