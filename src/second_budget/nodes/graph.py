"""The elicitation loop as a bounded cyclic graph.

    elicitor  ->  engine  ->  (frontier still open?)  ->  elicitor  ->  ...
                     |
                     +------ (frontier closed) ------->  drafter

The cycle is not decoration. Which fact to ask for next depends on which facts
are already established -- learning that a member is elderly *adds* a
medical-expense requirement that did not exist a moment earlier -- so the number
of rounds is not knowable in advance. The loop ends when ``frontier.is_closed``
returns true: a Python predicate over a fact ledger, not the model announcing
that it feels finished.

Three things here are load-bearing, and each was verified against the SDK rather
than taken from the documentation.

**The edge condition's parameter must be spelled ``invocation_state``.**
Strands decides the calling convention by inspecting the parameter *name*
(``multiagent/graph.py:97``). Rename it to ``ctx=None`` and there is no error and
no warning -- the condition is simply called with the legacy one-argument form
and your default is used forever. Measured on this exact graph: a 3-iteration
convergent loop became a 12-node runaway that only the execution cap stopped.
``**kwargs`` counts as the legacy form too, despite the protocol at
``graph.py:85`` declaring it.

**Conditions must be pure.** An edge condition is evaluated **twice** per
accepted traversal -- once in ``_is_node_ready_with_conditions``
(``graph.py:973``) and again in ``_build_node_input`` (``graph.py:1213``). A
condition that incremented a counter would count double.

**A limit breach does not raise.** On hitting ``max_node_executions`` the graph
sets ``status = FAILED`` and returns (``graph.py:787``); ``failed_nodes`` is an
``int`` and is **0**, and every node result still says ``completed``. The only
correct success check is ``result.status is Status.COMPLETED`` -- which is why
``run`` below checks exactly that and nothing else.
"""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.multiagent import GraphBuilder, Status
from strands.multiagent.graph import GraphState

from ..facts import FactLedger
from ..frontier import is_closed
from .solver_node import LEDGER_KEY, BudgetSolver

#: A cycle needs a ceiling. Ten elicitation rounds is far more than any real
#: case needs and still terminates a runaway in seconds.
MAX_NODE_EXECUTIONS = 14
EXECUTION_TIMEOUT_SECONDS = 180.0
NODE_TIMEOUT_SECONDS = 60.0


def _known(invocation_state: dict[str, Any] | None) -> dict:
    ledger: FactLedger | None = (invocation_state or {}).get(LEDGER_KEY)
    if ledger is None:
        return {}
    return {fact_id: ledger.value(fact_id) for fact_id in ledger.established}


def frontier_still_open(state: GraphState, *, invocation_state: dict[str, Any] | None = None) -> bool:
    """Loop back to the elicitor: the engine still needs facts.

    The parameter name ``invocation_state`` is the SDK's dispatch key. Do not
    rename it. Pure -- the SDK calls this more than once per traversal.
    """
    return not is_closed(_known(invocation_state))


def frontier_closed(state: GraphState, *, invocation_state: dict[str, Any] | None = None) -> bool:
    """Move on to drafting: everything the budget needs is established."""
    return is_closed(_known(invocation_state))


def build_graph(*, elicitor: Agent, drafter: Agent, engine: BudgetSolver | None = None):
    """Wire the loop. Returns a built ``Graph``."""
    engine = engine or BudgetSolver()

    builder = GraphBuilder()
    builder.add_node(elicitor, "elicitor")
    builder.add_node(engine, engine.id)
    builder.add_node(drafter, "drafter")

    builder.add_edge("elicitor", engine.id)
    builder.add_edge(engine.id, "elicitor", condition=frontier_still_open)
    builder.add_edge(engine.id, "drafter", condition=frontier_closed)

    # set_entry_point must follow add_node, and is mandatory here: in a cycle
    # every node has a dependency, so auto-detection finds no entry point and
    # build() raises (graph.py:463-470).
    builder.set_entry_point("elicitor")

    # Re-reading the ledger each round is the point. Without this the elicitor
    # carries its own previous turns forward and becomes the authority on what
    # is established, which is precisely the failure the ledger exists to stop.
    builder.reset_on_revisit(True)

    builder.set_max_node_executions(MAX_NODE_EXECUTIONS)
    builder.set_execution_timeout(EXECUTION_TIMEOUT_SECONDS)
    builder.set_node_timeout(NODE_TIMEOUT_SECONDS)
    builder.set_graph_id("second-budget")
    return builder.build()


class GraphDidNotConverge(RuntimeError):
    """The loop hit a limit instead of closing the frontier."""


def run(graph, task: str, invocation_state: dict[str, Any]):
    """Run the graph and refuse to treat a limit breach as success.

    ``result.failed_nodes`` is useless for this: it is an ``int`` and it is 0
    when a limit stops the graph, so ``if result.failed_nodes:`` misses the case
    entirely. Status is the only signal that carries it.
    """
    result = graph(task, invocation_state)
    if result.status is not Status.COMPLETED:
        raise GraphDidNotConverge(
            f"graph stopped with status={result.status.value} after "
            f"{len(result.execution_order)} node runs "
            f"(limit is {MAX_NODE_EXECUTIONS}); the fact frontier never closed"
        )
    return result
