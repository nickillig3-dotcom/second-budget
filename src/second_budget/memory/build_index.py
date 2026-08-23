"""Build a searchable, quotable index of 7 CFR 273 from the eCFR XML.

The packet this system produces quotes regulations. A paraphrase in a
fair-hearing request is not merely sloppy -- an agency representative reads the
cited text back off the actual regulation, and a paraphrase that shades the
meaning discredits the whole filing. So every quotation has to be a **literal
span** of the published text, attributed to a citation that resolves.

## Why the citations are section-level, and what was tried first

The obvious target was paragraph-level citations: ``7 CFR 273.10(e)(2)(ii)(C)``
rather than ``7 CFR 273.10``. eCFR does not publish them. It ships paragraphs as
a flat list of ``<P>`` elements whose only clue to depth is the marker each one
opens with, and the four marker alphabets (lower letter, digit, lower roman,
upper letter) alternate by depth, so in principle the path is recoverable.

In practice it is not, and the failure was measured rather than guessed. Two
things defeat it:

* eCFR packs several levels into one ``<P>``:
  ``"(e) Calculating net income-(1) Net monthly income. (i) To determine..."``
  is three citable levels in one element.
* ``(i)`` is both a lower letter and the first roman numeral, and the context
  that disambiguates it is exactly the depth being reconstructed.

Two rounds of increasingly careful heuristics -- consuming consecutive leading
markers, splitting on em-dash subdivisions, resolving ``(i)`` from the stack --
still produced **187 duplicate citations across 2,418 paragraphs**. A duplicate
citation means a path was recovered wrongly, so roughly eight percent of the
paragraph paths would have been wrong.

A wrong citation in a legal filing is worse than a coarse one. An agency
representative who looks up ``273.10(e)(1)`` and finds a rounding rule that is
not the one quoted has been handed a reason to dismiss the whole document. So
this indexes what the source actually carries: the **section**, which eCFR gives
explicitly as an attribute and which is therefore always right.

The property that matters survives intact. ``CitationGate`` requires a quote to
be a byte-identical span of the cited section's text -- a decidable predicate,
not a "the citation field is non-empty" check. The citation is coarser; the
quotation is exact, and the quotation is what an advocate argues from.

``paragraph_paths`` remains available for inspection, and ``collisions`` reports
the ambiguity, so the claim above can be checked rather than taken on trust.

    python -m second_budget.memory.build_index
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import re
import sqlite3
import xml.etree.ElementTree as ET

REPO = pathlib.Path(__file__).resolve().parents[3]
SOURCE = REPO / "data" / "corpus" / "ecfr_title7_part273.xml"
INDEX = REPO / "data" / "corpus" / "statute.sqlite"

_LEADING = re.compile(r"^\s*\(([A-Za-z0-9]{1,4})\)\s*")
_DASH_MARKER = re.compile(r"[—–-]\s*\((?=[A-Za-z0-9]{1,4}\))")
_ROMAN = re.compile(r"^[ivxlc]+$")

DEPTH = {"letter": 0, "digit": 1, "roman": 2, "upper": 3}


def _alphabet(marker: str) -> str:
    if marker.isdigit():
        return "digit"
    if marker == "i":
        return "letter-or-roman"
    if marker.islower():
        return "roman" if _ROMAN.fullmatch(marker) else "letter"
    return "upper"


def sections(xml_path: pathlib.Path = SOURCE):
    """Yield ``(citation, heading, text)`` for every section of 7 CFR 273.

    The citation comes from the ``N`` attribute eCFR puts on the section
    element. It is not inferred, so it cannot be wrong.
    """
    root = ET.parse(xml_path).getroot()
    for section in (e for e in root.iter() if e.get("TYPE") == "SECTION"):
        number = section.get("N")
        if not number:
            continue
        head_node = section.find("HEAD")
        heading = " ".join("".join(head_node.itertext()).split()) if head_node is not None else ""
        # Strip the section-symbol prefix eCFR puts in the heading itself.
        heading = re.sub(r"^\S*\s*" + re.escape(number) + r"\s*", "", heading).strip()
        body = [
            " ".join("".join(node.itertext()).split())
            for node in section.iter("P")
        ]
        yield f"7 CFR {number}", heading, "\n".join(p for p in body if p)


def paragraph_paths(xml_path: pathlib.Path = SOURCE):
    """Best-effort paragraph citations. **Not used for quotation.**

    Kept so the ambiguity documented above can be reproduced by a reader rather
    than believed. See ``collisions``.
    """
    root = ET.parse(xml_path).getroot()
    for section in (e for e in root.iter() if e.get("TYPE") == "SECTION"):
        number = section.get("N")
        if not number:
            continue
        stack: list[tuple[str, str]] = []
        for node in section.iter("P"):
            text = " ".join("".join(node.itertext()).split())
            if not text:
                continue
            parts = _DASH_MARKER.split(text)
            for fragment in [parts[0]] + ["(" + part for part in parts[1:]]:
                rest = fragment
                while (match := _LEADING.match(rest)) is not None:
                    marker = match.group(1)
                    alphabet = _alphabet(marker)
                    if alphabet == "letter-or-roman":
                        alphabet = "roman" if DEPTH["digit"] in {DEPTH[a] for a, _ in stack} else "letter"
                    depth = DEPTH[alphabet]
                    stack = [e for e in stack if DEPTH[e[0]] < depth]
                    stack.append((alphabet, marker))
                    rest = rest[match.end():]
                    if not rest.startswith("("):
                        break
                yield f"7 CFR {number}" + "".join(f"({m})" for _a, m in stack), fragment


def collisions(xml_path: pathlib.Path = SOURCE) -> dict[str, int]:
    """Paragraph citations the heuristic produces more than once.

    Non-empty, and that is the point: it is the evidence for indexing by section
    instead. Reported rather than assumed to be zero.
    """
    counts = collections.Counter(citation for citation, _ in paragraph_paths(xml_path))
    return {citation: n for citation, n in counts.items() if n > 1}


def build(xml_path: pathlib.Path = SOURCE, index_path: pathlib.Path = INDEX) -> int:
    if not xml_path.exists():
        raise SystemExit(
            f"{xml_path} not found. Fetch it with:\n"
            "  python -m second_budget.memory.fetch_cfr"
        )
    index_path.parent.mkdir(parents=True, exist_ok=True)
    if index_path.exists():
        index_path.unlink()

    rows = list(sections(xml_path))
    connection = sqlite3.connect(index_path)
    try:
        connection.execute(
            "CREATE VIRTUAL TABLE statute USING fts5("
            "citation, heading, text, tokenize='porter')"
        )
        connection.executemany(
            "INSERT INTO statute (citation, heading, text) VALUES (?, ?, ?)", rows
        )
        connection.commit()
    finally:
        connection.close()
    return len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=pathlib.Path, default=SOURCE)
    parser.add_argument("--index", type=pathlib.Path, default=INDEX)
    args = parser.parse_args()

    count = build(args.source, args.index)
    ambiguous = collisions(args.source)
    print(f"indexed {count} sections of 7 CFR 273 -> {args.index}")
    print(
        f"paragraph-path heuristic remains ambiguous on {len(ambiguous)} citations; "
        "citations are section-level for that reason"
    )
