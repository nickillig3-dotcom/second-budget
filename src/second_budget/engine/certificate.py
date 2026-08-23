"""The certificate: the closed set of numbers the model is permitted to state.

An anti-hallucination gate whose predicate is "the citation field is non-empty"
is theatre. This one is decidable: extract every currency-shaped token from what
the model is about to write, and require each to be a member of a set the engine
produced. Set membership -- nothing to interpret, nothing to argue with.

The certificate is built from a ``BudgetResult`` and frozen. It deliberately
contains **only** engine outputs and the figures the household or the notice
supplied, never anything the model wrote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .budget import BudgetResult

#: Currency-shaped tokens: $1,234, $1234.56, 1234.00, 1,234
_MONEY = re.compile(r"\$?\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\$?\d+(?:\.\d{1,2})?")


def money_tokens(text: str) -> tuple[float, ...]:
    """Every currency-shaped number in a string, as floats.

    Deliberately greedy. A gate that under-detects is worse than one that
    over-detects: a missed figure is an unchecked assertion in a legal filing,
    while a spurious one is a denial the model can answer by quoting the engine.
    """
    out = []
    for match in _MONEY.finditer(text):
        try:
            out.append(float(match.group(0).lstrip("$").replace(",", "")))
        except ValueError:  # pragma: no cover - the pattern makes this unreachable
            continue
    return tuple(out)


@dataclass(frozen=True)
class Certificate:
    """Values the engine computed, plus the stage and regulation each came from."""

    values: frozenset[float]
    stage_of: dict[float, str]
    cfr_of: dict[float, str]

    def permits(self, value: float) -> bool:
        return round(value, 2) in self.values

    def explain(self, value: float) -> str:
        key = round(value, 2)
        stage = self.stage_of.get(key)
        return f"{stage} ({self.cfr_of.get(key, '')})" if stage else "not computed"

    def unpermitted(self, text: str) -> tuple[float, ...]:
        """The figures in ``text`` that the engine never produced."""
        return tuple(v for v in money_tokens(text) if not self.permits(v))


def certify(*results: BudgetResult, also: dict[float, str] | None = None) -> Certificate:
    """Freeze one or more budget results into a certificate.

    Two results are the normal case: the engine's own derivation and the state's
    determination as re-expressed by the engine. Both sets are permitted, since
    the packet must be able to state what the agency decided as well as what the
    recomputation found.

    ``also`` admits figures that are legitimately quotable but not computed --
    the benefit printed on the notice, a household's stated rent -- each mapped
    to a label explaining where it came from.
    """
    values: set[float] = set()
    stage_of: dict[float, str] = {}
    cfr_of: dict[float, str] = {}

    for result in results:
        for stage in result.stages:
            key = round(float(stage.value), 2)
            values.add(key)
            stage_of.setdefault(key, stage.name)
            cfr_of.setdefault(key, stage.cfr)

    for value, label in (also or {}).items():
        key = round(float(value), 2)
        values.add(key)
        stage_of.setdefault(key, label)
        cfr_of.setdefault(key, "")

    return Certificate(
        values=frozenset(values),
        stage_of=stage_of,
        cfr_of=cfr_of,
    )
