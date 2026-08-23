"""Where a human belongs in the loop, and where a model call is wasted.

Two hooks, each answering a question the agent loop cannot answer for itself.

``BatchConfirmation`` -- a navigator confirms a whole fact batch in **one**
gate, not one prompt per fact.

Why this lives at the batch level rather than in an intervention: a ``Confirm``
returned from ``before_tool_call`` fires once per tool call and its payload
carries only *that* call. Three proposed facts give three interrupts in one
round -- measured, and worth stating precisely because an earlier version of
this comment claimed they *serialise* into three rounds, which does not
reproduce. Three separate payloads is still the wrong shape for a human: the
navigator wants to see the batch together, weigh the inferred facts against the
read ones, and reject selectively. ``BeforeToolsEvent`` is the only place where
the whole batch exists as one object.

``HaltWhenFrontierCloses`` -- when the recorded facts close the frontier, stop.
Without it the loop makes one more model call whose only possible output is "I
have everything", which is a paid round-trip to learn something a Python
predicate already knew.

Two sharp edges, both measured:

* ``AfterToolsEvent`` does **not** fire when ``BeforeToolsEvent`` raised an
  interrupt -- the interrupt check at ``event_loop/event_loop.py:796`` returns
  before the ``try/finally`` that dispatches it. Cleanup placed in
  ``AfterToolsEvent`` is skipped on exactly the path a human just approved.
* Interrupt names collide by **name**, not by id, within one dispatch
  (``hooks/registry.py:337``). Namespacing the name is what keeps two gates on
  one batch from raising ``ValueError``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from strands.hooks import AfterToolsEvent, BeforeToolsEvent, HookProvider, HookRegistry

from ..facts import FactId, FactLedger, Provenance
from ..frontier import is_closed

INTERRUPT_NAME = "second-budget:confirm-fact-batch"


def _batch_key(payload: dict) -> str:
    """A stable short key for one batch of proposed facts.

    Stable across a resume of the same batch, different between batches. See the
    comment at the interrupt call for why both properties are required.
    """
    material = json.dumps(
        [(f["fact_id"], f["value"], f["provenance"]) for f in payload["facts"]],
        sort_keys=True, default=str,
    )
    return hashlib.sha1(material.encode("utf-8")).hexdigest()[:12]


def _fact_calls(event: BeforeToolsEvent, tool_name: str) -> list[dict[str, Any]]:
    """The tool uses in this batch that propose a fact.

    ``BeforeToolsEvent`` carries the assistant ``message``, not a ready-made list
    of tool uses -- the batch is whatever ``toolUse`` blocks that message
    contains (``hooks/events.py``). Reading it out here keeps that shape in one
    place instead of spread across two hooks.
    """
    return [
        block["toolUse"]
        for block in event.message.get("content", [])
        if isinstance(block, dict)
        and "toolUse" in block
        and block["toolUse"].get("name") == tool_name
    ]


class BatchConfirmation(HookProvider):
    """One approval gate for a whole batch of proposed facts.

    The payload separates facts by provenance so the navigator can see at a
    glance which ones the model *inferred* rather than read -- those are the
    ones worth their attention, and they are visually separated for that reason.

    Resuming with a list of rejected fact ids removes them from the ledger,
    which re-opens the frontier and sends the graph round again. Approval is
    therefore not a formality: a partial rejection changes what happens next.
    """

    def __init__(self, ledger: FactLedger, *, navigator: str, tool_name: str = "record_fact") -> None:
        self.ledger = ledger
        self.navigator = navigator
        self.tool_name = tool_name
        self.batches_presented = 0
        self.gates_paused = 0
        self.rejected: list[str] = []

    def register_hooks(self, registry: HookRegistry, **_: Any) -> None:
        registry.add_callback(BeforeToolsEvent, self._confirm_batch)

    def _confirm_batch(self, event: BeforeToolsEvent) -> None:
        proposed = _fact_calls(event, self.tool_name)
        if not proposed:
            return

        self.batches_presented += 1
        payload = {
            "facts": [
                {
                    "fact_id": use["input"].get("fact_id"),
                    "value": use["input"].get("value"),
                    "provenance": use["input"].get("provenance"),
                    "source": use["input"].get("source", ""),
                    # The navigator's eye goes here first, and the UI renders it
                    # as a separate class.
                    "needs_scrutiny": use["input"].get("provenance") == Provenance.INFERRED.value,
                }
                for use in proposed
            ],
        }

        # The interrupt name has to be derived from the batch's CONTENT, and
        # getting this wrong twice is instructive.
        #
        # ``BeforeToolsEvent`` builds the interrupt id from the NAME alone
        # (``hooks/events.py``: uuid5 over the name), and an id that already
        # carries a response is returned rather than raised
        # (``types/interrupt.py``). So a fixed name gives every round the same
        # id, and a navigator's approval of the first batch silently covers the
        # second -- measured: three rounds produced one distinct id.
        #
        # A counter does not fix it either. The hook re-runs when the graph
        # resumes, so the counter has already moved on and the same batch gets a
        # fresh id, raises again, and the loop never terminates.
        #
        # Hashing the proposed facts gives both properties at once: stable
        # across the resume of one batch, distinct between batches.
        decision = event.interrupt(
            name=f"{INTERRUPT_NAME}:{_batch_key(payload)}", reason=payload
        )

        self.gates_paused += 1
        rejected = set(decision.get("rejected", []) if isinstance(decision, dict) else [])
        self.rejected.extend(sorted(rejected))
        if rejected:
            event.cancel = (
                "The navigator rejected: " + ", ".join(sorted(rejected))
                + ". Do not record these as stated; ask the household again."
            )


class HaltWhenFrontierCloses(HookProvider):
    """Stop the turn the moment the engine has everything it needs.

    Asserted in tests on the scripted model's call counter, because "the loop
    halted" is otherwise indistinguishable from "the model happened to stop".
    """

    def __init__(self, ledger: FactLedger) -> None:
        self.ledger = ledger
        self.halted = False

    def register_hooks(self, registry: HookRegistry, **_: Any) -> None:
        registry.add_callback(AfterToolsEvent, self._halt_if_closed)

    def _halt_if_closed(self, event: AfterToolsEvent) -> None:
        known: dict[FactId, object] = {
            fact_id: self.ledger.value(fact_id) for fact_id in self.ledger.established
        }
        if is_closed(known):
            self.halted = True
            event.end_turn = (
                "Every fact the budget needs is recorded. Handing off to the engine."
            )
