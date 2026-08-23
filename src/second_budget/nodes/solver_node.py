"""The budget engine as a graph node with no model inside it.

This is the architectural decision the whole project rests on. The budget is a
statute compiled to arithmetic. It is **not** exposed as a ``@tool``, because a
tool is something the model chooses to call, chooses arguments for, and can
decline. It is a peer node in the graph: the graph runs it, and what it returns
is not negotiable.

Two things about ``MultiAgentBase`` that the class layout does not advertise,
both verified against the SDK:

* Only ``invoke_async`` is abstract, but **the graph never calls it**. The graph
  calls ``stream_async`` (``multiagent/graph.py:1022``); the base implementation
  forwards to ``invoke_async`` (``multiagent/base.py:306``). Implement the one,
  leave the other alone.
* ``id`` is declared as a class annotation (``multiagent/base.py:227``) and
  ``__init__`` never assigns it. Forget to set it and ``add_node`` falls back to
  ``node_0`` (``graph.py:334``), and every lookup by name silently misses.

And the load-bearing one: ``_build_node_input`` (``graph.py:1162``) **stringifies**
a predecessor's output into the next node's prompt. Structured facts must
therefore travel in ``invocation_state``, which is the same dict object across
the whole graph run, not along the edges. Passing a ledger through the edge text
would hand the next node a rendering of the ledger instead of the ledger.
"""

from __future__ import annotations

import time
from typing import Any

from strands.agent.agent_result import AgentResult
from strands.multiagent import Status
from strands.multiagent.base import MultiAgentBase, MultiAgentResult, NodeResult
from strands.telemetry.metrics import EventLoopMetrics
from strands.types.event_loop import Metrics, Usage

from ..engine.budget import BudgetResult, Household, compute
from ..engine.certificate import Certificate, certify
from ..engine.constants import Constants, OutOfCoverage, for_state
from ..engine.rounding import medical_deduction
from ..facts import FactId, FactLedger
from ..frontier import is_closed, missing_facts

#: Keys this node reads from and writes to ``invocation_state``.
LEDGER_KEY = "second_budget.ledger"
CONSTANTS_KEY = "second_budget.constants"
RESULT_KEY = "second_budget.result"
FRONTIER_KEY = "second_budget.frontier"
CERTIFICATE_KEY = "second_budget.certificate"
REFUSAL_KEY = "second_budget.refusal"


class InsufficientFacts(Exception):
    """Not enough is established to run a budget. Carries what is still needed."""

    def __init__(self, missing: tuple[str, ...]) -> None:
        super().__init__(f"missing: {', '.join(missing)}")
        self.missing = missing


class BudgetSolver(MultiAgentBase):
    """Runs the budget, or reports precisely what is still missing.

    The node is total: it always returns a completed result. "I cannot compute
    this yet, and here is the frontier" is an answer, not a failure -- and it is
    the answer that drives the next elicitation round.
    """

    def __init__(self, node_id: str = "engine") -> None:
        super().__init__()
        self.id = node_id
        self.runs = 0

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **_: Any,
    ) -> MultiAgentResult:
        started = time.time()
        self.runs += 1

        state = invocation_state if invocation_state is not None else {}
        ledger: FactLedger = state.get(LEDGER_KEY) or FactLedger()
        known = {fact_id: ledger.value(fact_id) for fact_id in ledger.established}

        frontier = missing_facts(known)
        state[FRONTIER_KEY] = frontier

        if not is_closed(known):
            summary = self._render_frontier(frontier)
        else:
            try:
                result = self._run_budget(ledger, state)
            except OutOfCoverage as refusal:
                # A refusal is an answer, and a specific one. It names the
                # constant, the jurisdiction and where the published figure
                # lives, so a navigator learns something rather than hitting
                # "unsupported".
                state[REFUSAL_KEY] = refusal
                return self._completed(f"REFUSED. {refusal}", started)
            state[RESULT_KEY] = result
            certificate: Certificate = certify(
                result,
                also={float(known.get(FactId.STATE_DETERMINED_BENEFIT, 0.0) or 0.0):
                      "the benefit stated on the notice"},
            )
            state[CERTIFICATE_KEY] = certificate
            summary = self._render_result(result)

        return self._completed(summary, started)

    # -- internals --------------------------------------------------------

    def _run_budget(self, ledger: FactLedger, state: dict[str, Any]) -> BudgetResult:
        value = ledger.value
        # The constants come from the household's own facts, not from the
        # caller. Anything else would let a wrong jurisdiction be passed in
        # alongside a right one and produce a plausible answer.
        constants: Constants = state.get(CONSTANTS_KEY) or for_state(str(value(FactId.STATE)))
        state[CONSTANTS_KEY] = constants
        homeless = bool(value(FactId.HOMELESS_STATUS))
        shelter = 0.0 if homeless else (
            float(value(FactId.SHELTER_COST)) + float(value(FactId.UTILITY_ALLOWANCE))
        )
        elderly_or_disabled = bool(value(FactId.ELDERLY_OR_DISABLED))
        # The household reports what it SPENT; the budget wants the deductible
        # part. Only the amount above $35 counts, and only when an elderly or
        # disabled member is present. Applying that here rather than asking the
        # elicitor for a net figure keeps the rule in code -- a model that nets a
        # threshold in its head is computing, and computing is not its job.
        medical = (
            medical_deduction(
                float(value(FactId.MEDICAL_EXPENSES)),
                threshold=constants.medical_expense_threshold,
            )
            if elderly_or_disabled
            else 0.0
        )
        size = int(value(FactId.HOUSEHOLD_SIZE))
        return compute(
            Household(
                size=size,
                earned_income=float(value(FactId.EARNED_INCOME)),
                unearned_income=float(value(FactId.UNEARNED_INCOME)),
                standard_deduction=float(constants.standard_deduction(size)),
                dependent_care_expenses=float(value(FactId.DEPENDENT_CARE)),
                medical_expenses=medical,
                child_support_paid=float(value(FactId.CHILD_SUPPORT_PAID)),
                shelter_expenses=shelter,
                has_elderly_or_disabled_member=elderly_or_disabled,
                homeless_receiving_standard_deduction=homeless,
                homeless_shelter_deduction=constants.homeless_shelter_deduction,
                shelter_cap=constants.shelter_cap(
                    has_elderly_or_disabled_member=elderly_or_disabled
                ),
                max_allotment=constants.max_allotment(size),
                minimum_benefit=constants.minimum_benefit(),
            )
        )

    @staticmethod
    def _render_frontier(frontier: tuple) -> str:
        lines = ["INSUFFICIENT. The budget still needs:"]
        lines += [f"  - {r.fact.value}: {r.because}" for r in frontier]
        return "\n".join(lines)

    @staticmethod
    def _render_result(result: BudgetResult) -> str:
        lines = [f"COMPUTED. Allotment ${result.allotment}."]
        lines += [
            f"  {stage.name} = {stage.value:g}  [{stage.cfr}]"
            + (f"  -- {stage.note}" if stage.note else "")
            for stage in result.stages
        ]
        return "\n".join(lines)

    def _completed(self, text: str, started: float) -> MultiAgentResult:
        elapsed = round((time.time() - started) * 1000)
        # Status must be set explicitly: MultiAgentResult defaults to PENDING
        # (multiagent/base.py:146) and the graph copies the field verbatim
        # without validating it (graph.py:1037).
        return MultiAgentResult(
            status=Status.COMPLETED,
            execution_count=1,
            execution_time=elapsed,
            accumulated_usage=Usage(inputTokens=0, outputTokens=0, totalTokens=0),
            accumulated_metrics=Metrics(latencyMs=elapsed),
            results={
                self.id: NodeResult(
                    result=AgentResult(
                        stop_reason="end_turn",
                        message={"role": "assistant", "content": [{"text": text}]},
                        metrics=EventLoopMetrics(),
                        state={},
                    ),
                    status=Status.COMPLETED,
                    execution_time=elapsed,
                    execution_count=1,
                )
            },
        )
