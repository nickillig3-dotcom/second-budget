"""The elicitor: turns what a household said into typed, attributed facts.

This is the model's real job in the system, and it is the one thing here a model
is genuinely better at than code. A navigator hears:

    "I get paid every two weeks, sometimes I pick up a shift, my mother moved in
     and pays some of the rent, and I paid sixty dollars for my inhaler."

and has to land that on roughly a dozen typed regulatory facts. That mapping is
the task navigators actually perform today, on paper, and it is the task they
get wrong. It is not arithmetic and it does not belong in the engine.

What the elicitor may **not** do is decide anything. It records facts with a
provenance; the engine decides what they add up to, and the frontier decides what
to ask next.
"""

from __future__ import annotations

from strands import Agent, tool
from strands.models.model import Model

from ..facts import Fact, FactId, FactLedger, Provenance

SYSTEM_PROMPT = """\
You are helping a benefits navigator turn a household's own words into typed facts
for a SNAP budget.

Rules you cannot talk your way around:

1. Record facts with `record_fact`. Never state a benefit amount, a deduction, or
   any computed figure yourself -- you do not compute. The engine does.
2. Every fact needs an honest provenance:
     from-notice     - you read it on the notice of action
     from-narrative  - the household said it
     inferred        - you worked it out. These pause for a human signature, so
                       use the label truthfully; mislabelling an inference as a
                       reading is the one failure that cannot be caught later.
3. Record only what you were actually told. If the household did not mention
   child support, that is a fact you do not have -- not a zero.
4. Record amounts exactly as the household states them. Never subtract a
   threshold, never annualise, never prorate -- the engine applies every such
   rule and it applies them the way the regulation words them.
5. You will be told which facts are still missing. Ask about those, nothing else.
"""


def build_ledger_tools(ledger: FactLedger):
    """Tools closed over one ledger.

    The ledger is shared with the engine node through ``invocation_state``; the
    closure means the tool writes to the same object the engine reads, with no
    serialisation step in between where a fact could quietly change shape.
    """

    @tool
    def record_fact(fact_id: str, value: float | bool | str, provenance: str,
                    source: str = "") -> str:
        """Record one fact about the household.

        Args:
            fact_id: which fact, e.g. "income.earned" or "household.size".
            value: the value. Numbers are monthly dollars.
            provenance: "from-notice", "from-narrative" or "inferred".
            source: the notice line or the household's own words.
        """
        try:
            typed_id = FactId(fact_id)
        except ValueError:
            allowed = ", ".join(f.value for f in FactId)
            return f"REJECTED: {fact_id!r} is not a fact this budget uses. Allowed: {allowed}"
        try:
            typed_provenance = Provenance(provenance)
        except ValueError:
            return (
                f"REJECTED: provenance must be one of "
                f"{', '.join(p.value for p in Provenance)}, not {provenance!r}"
            )

        ledger.record(
            Fact(id=typed_id, value=value, provenance=typed_provenance, source=source)
        )
        return f"recorded {typed_id.value} = {value!r} ({typed_provenance.value})"

    return [record_fact]


def build_elicitor(model: Model, ledger: FactLedger, *, hooks=None,
                   interventions=None) -> Agent:
    return Agent(
        model=model,
        tools=build_ledger_tools(ledger),
        system_prompt=SYSTEM_PROMPT,
        hooks=hooks or [],
        interventions=interventions or [],
        callback_handler=None,
    )
