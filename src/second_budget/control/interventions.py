"""The control plane: what the model is structurally forbidden to do.

Second Budget drafts a fair-hearing request. A hallucinated dollar figure in that
document does not merely embarrass anyone -- it loses the hearing and burns the
household's one appeal. So the constraint cannot be a line in a system prompt
that the model is asked to respect. It has to be a gate the model cannot reach
around.

Two design decisions here are worth defending, because both were measured
against the SDK rather than assumed:

**The gates sit on ``before_tool_call``, not on ``after_model_call``.**
``Guide`` at ``after_model_call`` appends straight into ``agent.messages``
(``interventions/registry.py:182``) instead of going through
``Agent._append_messages`` (``agent/agent.py:1706``), so no ``MessageAddedEvent``
fires and **no session manager records it**. Measured with a real
``FileSessionManager``: after a restore the guidance is gone while the model's
uncorrected output remains. A ``Deny`` at ``before_tool_call`` goes through the
tool-result path and survives the restore. For a case a navigator reopens days
later, guidance-based control is not weaker -- it is absent.

**``on_error`` is set to ``"deny"``.** The SDK default is ``"throw"``, which is
fail-closed but kills the whole invocation; ``"proceed"`` is the one fail-open
value and would write the forbidden number. ``"deny"`` blocks *and* leaves the
agent alive with a legible tool result, which is what a navigator in the middle
of a case needs.

Note what these gates deliberately do **not** claim: an intervention cannot
block the model's prose. ``Deny`` at ``after_model_call`` is a silent no-op
(``interventions/registry.py:191``, logs a warning and nothing else). Every
number that reaches the packet therefore has to pass through a *tool*, which is
why the drafter writes by calling tools rather than by returning text.
"""

from __future__ import annotations

import json
from typing import Any

from strands import InterventionHandler
from strands.interventions import Confirm, Deny, InterventionAction, OnError, Proceed

from ..engine.certificate import Certificate
from ..facts import Provenance


def _payload_text(tool_use: dict[str, Any]) -> str:
    """Everything the model is about to write, flattened for inspection."""
    return json.dumps(tool_use.get("input", {}), default=str)


class NumbersGate(InterventionHandler):
    """The model may not state a figure the engine did not compute.

    The predicate is set membership over the certificate, so it is decidable and
    unit-testable. The denial reason names the offending figure and lists what
    *is* available -- the reason string is the model's entire steering signal
    (the framework imposes no retry limit), so it is written as an instruction
    rather than a complaint.
    """

    name = "numbers-are-not-yours"

    def __init__(self, certificate: Certificate, *, guarded_tools: frozenset[str]) -> None:
        self.certificate = certificate
        self.guarded_tools = guarded_tools
        self.denials: list[tuple[str, tuple[float, ...]]] = []

    @property
    def on_error(self) -> OnError:
        # Fail closed, but keep the case alive. See the module docstring.
        return "deny"

    def before_tool_call(self, event: Any, **_: Any) -> InterventionAction:
        tool_use = event.tool_use
        if tool_use.get("name") not in self.guarded_tools:
            return Proceed()

        offending = self.certificate.unpermitted(_payload_text(tool_use))
        if not offending:
            return Proceed()

        self.denials.append((tool_use.get("name", ""), offending))
        available = ", ".join(
            f"{value:g} ({self.certificate.explain(value)})"
            for value in sorted(self.certificate.values)
        )
        return Deny(
            reason=(
                f"{', '.join(format(v, 'g') for v in offending)} was not computed by the "
                f"engine and may not appear in a filing. Use one of these values, or call "
                f"the engine again: {available}"
            )
        )


class InferredFactGate(InterventionHandler):
    """A fact the model inferred needs a human signature before it is used.

    Returns ``Confirm`` with no response, which drops into the interrupt system
    and pauses the loop for the navigator.

    Two sharp edges, both verified: ``Confirm(response=None)`` means *ask a
    human*, not *deny* -- a handler that computes ``response=lookup()`` and gets
    ``None`` back pauses rather than blocks. And several ``Confirm``s in one
    dispatch produce **sequential** interrupt rounds, not one batch, which is why
    batching lives in the hook layer instead (see ``hooks.py``).
    """

    name = "inferred-facts-need-a-signature"

    def __init__(self, *, tool_name: str = "record_fact") -> None:
        self.tool_name = tool_name
        self.confirmations_requested: list[str] = []

    @property
    def on_error(self) -> OnError:
        return "deny"

    def before_tool_call(self, event: Any, **_: Any) -> InterventionAction:
        tool_use = event.tool_use
        if tool_use.get("name") != self.tool_name:
            return Proceed()

        payload = tool_use.get("input", {})
        if payload.get("provenance") != Provenance.INFERRED.value:
            return Proceed()

        fact_id = str(payload.get("fact_id", "?"))
        self.confirmations_requested.append(fact_id)
        return Confirm(
            prompt=(
                f"The model inferred {fact_id} = {payload.get('value')!r} rather than "
                f"reading it. Confirm before it enters the budget."
            ),
            reason={"fact_id": fact_id, "value": payload.get("value"),
                    "provenance": Provenance.INFERRED.value},
        )


class CitationGate(InterventionHandler):
    """A quotation must be a literal span of the section it cites.

    This is the difference between an anti-hallucination gate and the appearance
    of one. A check that the citation field is non-empty stops nothing; this
    normalises whitespace and asks whether the quoted text is a **substring** of
    the published section. Set membership again, in a different clothing:
    decidable, unit-testable, and impossible to talk around.

    It matters because of who reads the output. An agency representative at a
    fair hearing looks the cited paragraph up and reads it back. A quotation that
    has been smoothed into better English is not a small error -- it is a reason
    to dismiss the filing, and it costs the household the appeal.

    The store is asked whether it is healthy before any quotation is allowed
    through, because a silently empty regulation index would otherwise let every
    quote fail for the same reason and produce a document full of denials that
    look like the model's fault.
    """

    name = "quotes-must-be-verbatim"

    def __init__(self, store: Any, *, guarded_tools: frozenset[str],
                 citation_field: str = "citation", quote_field: str = "quote") -> None:
        self.store = store
        self.guarded_tools = guarded_tools
        self.citation_field = citation_field
        self.quote_field = quote_field
        self.denials: list[tuple[str, str]] = []

    @property
    def on_error(self) -> OnError:
        return "deny"

    @staticmethod
    def _normalise(text: str) -> str:
        return " ".join(text.split())

    def before_tool_call(self, event: Any, **_: Any) -> InterventionAction:
        tool_use = event.tool_use
        if tool_use.get("name") not in self.guarded_tools:
            return Proceed()

        payload = tool_use.get("input", {})
        quote = payload.get(self.quote_field)
        if not quote:
            return Proceed()

        # A lookup that failed earlier makes every later quote unverifiable.
        self.store.assert_healthy()

        citation = str(payload.get(self.citation_field, "")).strip()
        if not citation:
            self.denials.append(("", str(quote)[:80]))
            return Deny(
                reason=(
                    "a quotation needs the citation it came from. Name the section, "
                    f"for example '7 CFR 273.9', in the '{self.citation_field}' field."
                )
            )

        source = self.store.section_text(citation)
        if source is None:
            self.denials.append((citation, str(quote)[:80]))
            return Deny(
                reason=(
                    f"{citation!r} is not a section of 7 CFR 273 in the index. "
                    f"Cite one of: {', '.join(self.store.citations()[:6])}, ..."
                )
            )

        if self._normalise(str(quote)) not in self._normalise(source):
            self.denials.append((citation, str(quote)[:80]))
            return Deny(
                reason=(
                    f"that text is not a literal span of {citation}; it was "
                    f"paraphrased. Quote the regulation word for word, or quote a "
                    f"shorter span that you can reproduce exactly."
                )
            )

        return Proceed()
