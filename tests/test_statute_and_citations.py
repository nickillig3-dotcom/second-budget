"""Quotations are literal, and a broken index refuses rather than going quiet.

The failure this file guards against is specific and measured: Strands'
``MemoryManager`` catches a store's exception and returns an empty list
(``memory/memory_manager.py:353``), so a dead regulation index is
indistinguishable from a regulation that says nothing. In a benefits filing,
"no rule requires that" produced by a broken lookup is the worst available
outcome.
"""

from __future__ import annotations

import asyncio
import pathlib
import sqlite3

import pytest
from strands import Agent, tool

from second_budget.control.interventions import CitationGate
from second_budget.memory.build_index import build, collisions
from second_budget.memory.statute_store import (
    StatuteIndexUnhealthy,
    StatuteStore,
    format_for_prompt,
)
from second_budget.models.scripted import ScriptedModel, Turn

INDEX = pathlib.Path(__file__).resolve().parents[1] / "data" / "corpus" / "statute.sqlite"
VERBATIM = "Twenty percent of gross earned income"
requires_index = pytest.mark.skipif(
    not INDEX.exists(),
    reason="statute index not built: python -m second_budget.memory.build_index",
)


@pytest.fixture()
def store() -> StatuteStore:
    s = StatuteStore()
    asyncio.run(s.initialize())
    return s


# -- the index --------------------------------------------------------------


@requires_index
def test_the_sections_the_engine_cites_are_all_present(store) -> None:
    for citation in ("7 CFR 273.9", "7 CFR 273.10"):
        assert store.section_text(citation), f"{citation} missing from the index"


@requires_index
@pytest.mark.parametrize(
    ("citation", "span"),
    [
        ("7 CFR 273.9", VERBATIM),
        ("7 CFR 273.9", "in excess of $35 per month"),
        ("7 CFR 273.9", "in excess of 50 percent of the household"),
        ("7 CFR 273.10", "round the 30 percent of net income up to the nearest higher dollar"),
        ("7 CFR 273.10", "one-person and two-person households shall receive"),
    ],
)
def test_every_rule_the_engine_implements_can_be_quoted_verbatim(store, citation, span) -> None:
    """If a rule cannot be quoted, the packet cannot defend the number it produced."""
    assert span in store.section_text(citation)


def test_the_paragraph_path_heuristic_is_still_ambiguous() -> None:
    """The evidence for section-level citations, kept reproducible.

    Two rounds of heuristics left 187 duplicate paragraph citations. A duplicate
    means a path was recovered wrongly, and a wrong citation in a filing is worse
    than a coarse one -- so citations are section-level and this test exists so
    the reason can be checked rather than believed. If it ever reaches zero, the
    decision is worth revisiting.
    """
    ambiguous = collisions()
    assert len(ambiguous) > 0
    assert "7 CFR 273.2(b)(1)" in ambiguous


# -- failing closed ---------------------------------------------------------


def test_a_missing_index_refuses_at_construction(tmp_path) -> None:
    absent = StatuteStore(tmp_path / "nope.sqlite")
    with pytest.raises(StatuteIndexUnhealthy) as refusal:
        asyncio.run(absent.initialize())
    assert "build_index" in str(refusal.value)


def test_an_empty_index_fails_the_canary_rather_than_answering_nothing(tmp_path) -> None:
    """An index that returns no rows is the dangerous case: without the canary
    the agent proceeds and concludes the regulation is silent."""
    path = tmp_path / "empty.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("CREATE VIRTUAL TABLE statute USING fts5(citation, heading, text)")
    connection.commit()
    connection.close()

    with pytest.raises(StatuteIndexUnhealthy) as refusal:
        asyncio.run(StatuteStore(path).initialize())
    assert "canary" in str(refusal.value)


@requires_index
def test_a_lookup_failure_latches_and_blocks_later_quotation(store) -> None:
    """A startup probe only proves the index was alive at startup."""
    store.assert_healthy()

    store.searches_attempted += 1  # a search that began and never completed
    with pytest.raises(StatuteIndexUnhealthy):
        store.assert_healthy()


@requires_index
def test_the_regulation_refuses_to_be_written_to(store) -> None:
    """Silently ignoring a write would be worse than refusing one."""
    with pytest.raises(StatuteIndexUnhealthy):
        asyncio.run(store.add_messages([]))


@requires_index
def test_search_returns_the_citation_alongside_the_text(store) -> None:
    entries = asyncio.run(store.search("excess shelter deduction"))
    assert entries
    assert all(e.metadata.get("citation", "").startswith("7 CFR 273.") for e in entries)


@requires_index
def test_the_prompt_format_carries_the_citation(store) -> None:
    """The SDK's default injection format discards metadata, so the section
    number never reaches the model and a gate demanding attribution becomes
    unsatisfiable. A custom format is not optional here."""
    entries = asyncio.run(store.search("excess shelter deduction"))
    rendered = format_for_prompt(entries)

    assert 'citation="7 CFR 273.' in rendered
    assert "verbatim" in rendered


# -- the gate ---------------------------------------------------------------


def _drafting_agent(store, turns):
    written: list[tuple[str, str]] = []

    @tool
    def cite(citation: str, quote: str) -> str:
        """Add a quoted regulation to the filing."""
        written.append((citation, quote))
        return "added"

    gate = CitationGate(store, guarded_tools=frozenset({"cite"}))
    model = ScriptedModel(turns)
    agent = Agent(model=model, tools=[cite], interventions=[gate], callback_handler=None)
    return agent, model, written, gate


@requires_index
def test_a_verbatim_quotation_is_allowed_through(store) -> None:
    agent, _model, written, gate = _drafting_agent(
        store, [Turn.tool("cite", {"citation": "7 CFR 273.9", "quote": VERBATIM}),
                Turn.say("done")]
    )
    agent("Cite the earned income deduction.")

    assert written == [("7 CFR 273.9", VERBATIM)]
    assert gate.denials == []


@requires_index
def test_a_paraphrase_is_denied_and_the_model_is_told_why(store) -> None:
    paraphrase = "Twenty percent of your earned income is deducted."
    agent, model, written, gate = _drafting_agent(
        store,
        [Turn.tool("cite", {"citation": "7 CFR 273.9", "quote": paraphrase}),
         Turn.tool("cite", {"citation": "7 CFR 273.9", "quote": VERBATIM}),
         Turn.say("done")],
    )
    agent("Cite it.")

    assert written == [("7 CFR 273.9", VERBATIM)], "the paraphrase reached the filing"
    assert len(gate.denials) == 1
    results = [
        block["toolResult"]
        for message in agent.messages
        for block in message.get("content", [])
        if isinstance(block, dict) and "toolResult" in block
    ]
    assert results[0]["status"] == "error"
    assert "paraphrased" in results[0]["content"][0]["text"]


@requires_index
def test_a_citation_that_is_not_in_the_index_is_denied(store) -> None:
    agent, _model, written, gate = _drafting_agent(
        store, [Turn.tool("cite", {"citation": "7 CFR 273.99", "quote": VERBATIM}),
                Turn.say("giving up")]
    )
    agent("Cite it.")

    assert written == []
    assert gate.denials == [("7 CFR 273.99", VERBATIM)]


@requires_index
def test_whitespace_differences_do_not_count_as_paraphrase(store) -> None:
    """Line wrapping is not a change of meaning; a gate that treated it as one
    would deny honest quotations and teach the model to stop quoting."""
    agent, _model, written, gate = _drafting_agent(
        store,
        [Turn.tool("cite", {"citation": "7 CFR 273.9",
                            "quote": "Twenty  percent\n of gross\tearned income"}),
         Turn.say("done")],
    )
    agent("Cite it.")

    assert len(written) == 1
    assert gate.denials == []


@requires_index
def test_a_quotation_is_refused_while_the_index_is_unhealthy(store) -> None:
    store.searches_attempted += 1  # simulate a lookup that never completed
    agent, _model, written, _gate = _drafting_agent(
        store, [Turn.tool("cite", {"citation": "7 CFR 273.9", "quote": VERBATIM}),
                Turn.say("done")]
    )
    agent("Cite it.")

    # on_error is "deny", so the case survives but nothing was quoted.
    assert written == []


def test_the_index_can_be_rebuilt_from_the_committed_source(tmp_path) -> None:
    source = pathlib.Path(__file__).resolve().parents[1] / "data" / "corpus" / "ecfr_title7_part273.xml"
    if not source.exists():
        pytest.skip("eCFR source not fetched")
    count = build(source, tmp_path / "rebuilt.sqlite")
    assert count == 32
