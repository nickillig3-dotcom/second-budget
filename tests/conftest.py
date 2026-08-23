"""Build the statute index on demand so a fresh clone proves the whole claim.

The index is derived from the committed eCFR XML, so it is not itself
committed -- but a test suite that quietly skips the quotation tests would let
the strongest claim in this repository go unchecked on exactly the machine that
matters: someone else's.

This runs at import rather than in a fixture on purpose. ``skipif`` markers are
evaluated while tests are being collected, which happens after conftest is
imported but before any fixture runs -- so a fixture here would build the index
just too late to stop fifteen tests from skipping.
"""

from __future__ import annotations

import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
SOURCE = REPO / "data" / "corpus" / "ecfr_title7_part273.xml"
INDEX = REPO / "data" / "corpus" / "statute.sqlite"

if SOURCE.exists() and not INDEX.exists():
    from second_budget.memory.build_index import build

    build(SOURCE, INDEX)
