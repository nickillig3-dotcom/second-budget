"""7 CFR 273 as a Strands ``MemoryStore``, wired to fail closed.

The regulation is an authority, not a memory. It is never written to at runtime,
nothing is extracted from conversations into it, and the only thing it does is
hand back literal spans of published text with the section they came from.

Three properties here are not stylistic. Each corresponds to a specific way the
SDK's memory path fails **silently**, measured against the installed source:

**The startup canary.** ``MemoryManager.search`` catches a store's exception,
logs a warning, and returns an empty list (``memory/memory_manager.py:353``). A
dead index and an index that genuinely contains nothing are byte-identical to
both the agent and the caller -- so a broken regulation lookup does not surface
as an error, it surfaces as *"no rule requires that"*. In a benefits filing that
is the worst possible failure. ``initialize`` is the one hook in the memory path
that is not guarded (``plugins/registry.py``), so a raise there aborts
``Agent(...)`` construction. The canary lives there deliberately.

**The per-search latch.** A startup probe only proves the index was alive at
startup. If the backend dies mid-case the manager will not say so, so the store
records its own failures and ``assert_healthy`` is called before anything is
allowed to quote.

**Thread-local connections.** Strands routes ``init_agent`` and each search
through ``run_async``, which builds a fresh ``ThreadPoolExecutor`` every time --
so the constructor, ``initialize`` and ``search`` run on three different threads.
A single shared ``sqlite3`` connection raises ``ProgrammingError``, which the
manager then swallows into "the regulation is silent". ``check_same_thread`` is
left **on**: the guard is what would have reported this, and disabling it to make
the symptom go away would have hidden the cause.
"""

from __future__ import annotations

import pathlib
import sqlite3
import threading
from typing import Any

from strands.memory import MemoryEntry, MemoryStore

INDEX = pathlib.Path(__file__).resolve().parents[3] / "data" / "corpus" / "statute.sqlite"

#: A query whose answer is known, and the text that must come back. If the index
#: cannot answer this, it cannot be trusted to answer anything.
CANARY_QUERY = "excess shelter deduction 50 percent"
CANARY_MUST_CONTAIN = "in excess of 50 percent of the household"


class StatuteIndexUnhealthy(RuntimeError):
    """The regulation index cannot be trusted. Nothing may be quoted."""


class StatuteStore(MemoryStore):
    """Read-only, section-attributed access to 7 CFR 273."""

    def __init__(self, index_path: pathlib.Path = INDEX, *, name: str = "cfr273") -> None:
        self.name = name
        self.description = (
            "Verbatim text of 7 CFR 273 (SNAP certification of eligible households), "
            "attributed by section."
        )
        self.max_search_results = 3
        self.writable = False       # a regulation is not a memory
        self.extraction = None      # ...so extraction must stay off
        self._index_path = index_path
        self._local = threading.local()
        self.searches_attempted = 0
        self.searches_succeeded = 0
        self.last_error: BaseException | None = None

    # -- connection -------------------------------------------------------

    def _connection(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            if not self._index_path.exists():
                raise StatuteIndexUnhealthy(
                    f"{self._index_path} does not exist. Build it with:\n"
                    "  python -m second_budget.memory.build_index"
                )
            connection = sqlite3.connect(self._index_path, check_same_thread=True)
            connection.execute("PRAGMA query_only = ON")
            self._local.connection = connection
        return connection

    # -- MemoryStore ------------------------------------------------------

    async def initialize(self) -> None:
        """Prove the index answers a question whose answer is known.

        A raise here aborts agent construction, which is the intended and only
        fail-closed seam in the memory path.
        """
        try:
            hits = self._query(CANARY_QUERY, limit=5)
        except Exception as exc:  # noqa: BLE001 - any failure is disqualifying
            raise StatuteIndexUnhealthy(
                f"{self.name}: the regulation index could not be read: {exc}"
            ) from exc
        if not any(CANARY_MUST_CONTAIN in text for _citation, _heading, text in hits):
            raise StatuteIndexUnhealthy(
                f"{self.name}: the regulation index failed its startup canary "
                f"({CANARY_QUERY!r} did not return the excess shelter rule). "
                f"Rebuild it: python -m second_budget.memory.build_index"
            )

    async def search(self, query: str, options: dict[str, Any] | None = None) -> list[MemoryEntry]:
        limit = (options or {}).get("max_search_results", self.max_search_results)
        self.searches_attempted += 1
        try:
            hits = self._query(query, limit=limit)
        except BaseException as exc:  # noqa: BLE001
            # MemoryManager would swallow this. Latch it first so
            # assert_healthy can refuse to let anything be quoted.
            self.last_error = exc
            raise
        self.searches_succeeded += 1
        return [
            MemoryEntry(content=text, metadata={"citation": citation, "heading": heading})
            for citation, heading, text in hits
        ]

    async def add_messages(self, messages: Any, context: Any = None) -> None:
        """A regulation is never written to. Silently ignoring a write would be
        worse than refusing one."""
        raise StatuteIndexUnhealthy(
            f"{self.name} is read-only: 7 CFR 273 is an authority, not a memory store"
        )

    # -- for the citation gate --------------------------------------------

    def section_text(self, citation: str) -> str | None:
        """The full published text of one section, for an exact-span check."""
        row = self._connection().execute(
            "SELECT text FROM statute WHERE citation = ?", (citation.strip(),)
        ).fetchone()
        return row[0] if row else None

    def citations(self) -> list[str]:
        return [
            row[0]
            for row in self._connection().execute(
                "SELECT citation FROM statute ORDER BY citation"
            )
        ]

    def assert_healthy(self) -> None:
        """Refuse to proceed if any lookup has failed since startup."""
        if self.last_error is not None:
            raise StatuteIndexUnhealthy(
                f"{self.name}: a regulation lookup failed during this case; "
                f"nothing may be quoted"
            ) from self.last_error
        if self.searches_attempted != self.searches_succeeded:
            raise StatuteIndexUnhealthy(
                f"{self.name}: {self.searches_attempted - self.searches_succeeded} "
                f"regulation lookup(s) did not complete"
            )

    # -- internals --------------------------------------------------------

    def _query(self, query: str, *, limit: int) -> list[tuple[str, str, str]]:
        terms = " OR ".join(
            f'"{word}"' for word in query.replace('"', " ").split() if len(word) > 2
        )
        if not terms:
            return []
        return list(
            self._connection().execute(
                "SELECT citation, heading, text FROM statute "
                "WHERE statute MATCH ? ORDER BY rank LIMIT ?",
                (terms, limit),
            )
        )


def format_for_prompt(entries: list[MemoryEntry]) -> str:
    """Render retrieved regulation with its citation attached.

    The SDK's default injection format renders only ``content`` plus the store's
    name and **discards ``metadata``** (``memory/memory_manager.py:735``), so the
    section number never reaches the model. A gate that demands section
    attribution is unsatisfiable with the default; a custom format is not
    optional here.
    """
    spans = "\n".join(
        f'<span citation="{entry.metadata.get("citation", "")}">{entry.content}</span>'
        for entry in entries
    )
    return (
        "<regulation>\n" + spans + "\n</regulation>\n"
        "Quote only from the text above, verbatim, and name the citation it came from."
    )
