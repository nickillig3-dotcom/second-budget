"""Layer B -- does an independent recomputation predict what a federal reviewer found?

Layer A proves the engine reproduces the *reviewed* benefit. That is a statement
about arithmetic. This layer asks the question the product actually exists to
answer, and answers it against real federal records:

    A household hands you the benefit on its notice. You recompute it
    independently. Your answer disagrees. Does that mean anything?

The USDA QC file lets this be measured rather than argued, because it carries
both sides plus a federal reviewer's verdict:

    RAWBEN   (origin R) "Reported SNAP benefit received"  -- what the household got
    FSBEN    (origin C) "Final calculated benefit"        -- the reviewed computation
    ELEMENT1..9         the reviewer's error findings, empty when nothing was found
    AMTERR   (origin R) "the difference between the benefits the State agency
                         authorized and the benefits the State agency should
                         have authorized"

Definitions quoted from the FY2024 SNAP QC Technical Documentation
(snapqcdata.net, FY-2024-Tech-Doc.pdf, Chapter V codebook).

WHAT THIS DOES NOT CLAIM. Agreement is measured, causation is not. A reviewer's
finding can rest on facts the file does not expose to us, and a disagreement
here can equally be the engine's own limitation. The confusion matrix is
published in full, including both error cells, so the reader can see the size of
what is not explained.
"""
from __future__ import annotations

import argparse
import collections
import csv
import pathlib

from ..engine.allotment import allotment

REPO = pathlib.Path(__file__).resolve().parents[3]
QC_CSV = REPO / "data" / "qc" / "qc_pub_fy2024.csv"

SEPARATE_BENEFIT_SCHEDULE = {"Alaska", "Hawaii", "Guam", "Virgin Islands"}


def _num(v: str | None) -> float | None:
    v = (v or "").strip()
    if v in ("", ".", "NA"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _error_count(row: dict) -> int:
    els = [(row.get(f"ELEMENT{i}") or "").strip() for i in range(1, 10)]
    return len([e for e in els if e not in ("", ".", "NA", "0")])


def run() -> dict:
    # cells[(engine_agrees, reviewer_found_error)] -> count
    cells: collections.Counter[tuple[bool, bool]] = collections.Counter()
    amterr_exact = amterr_total = 0
    amterr_residual: collections.Counter[int] = collections.Counter()
    skipped = 0

    with open(QC_CSV, newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            if row.get("STATENAME", "").strip() in SEPARATE_BENEFIT_SCHEDULE:
                skipped += 1
                continue
            rawnet = _num(row["RAWNET"])
            rawben = _num(row["RAWBEN"])
            rawsz = _num(row["RAWHSIZE"])
            benmax = _num(row["BENMAX"])
            minben = _num(row["MINIMUM_BEN"])
            if None in (rawnet, rawben, rawsz, benmax, minben):
                skipped += 1
                continue

            recomputed = allotment(
                max_allotment=int(benmax),
                net_income=rawnet,
                household_size=int(rawsz),
                minimum_benefit=int(minben),
            ).allotment

            agrees = recomputed == int(rawben)
            has_error = _error_count(row) > 0
            cells[(agrees, has_error)] += 1

            # Secondary check: is AMTERR the gap between reported and reviewed?
            fsben, amterr = _num(row["FSBEN"]), _num(row["AMTERR"])
            if has_error and None not in (fsben, amterr):
                amterr_total += 1
                gap = abs(int(fsben) - int(rawben))
                if gap == int(amterr):
                    amterr_exact += 1
                else:
                    amterr_residual[gap - int(amterr)] += 1

    agree_clean = cells[(True, False)]
    agree_error = cells[(True, True)]
    disagree_clean = cells[(False, False)]
    disagree_error = cells[(False, True)]
    n_agree = agree_clean + agree_error
    n_disagree = disagree_clean + disagree_error

    return {
        "cells": cells,
        "n": n_agree + n_disagree,
        "skipped": skipped,
        "p_clean_given_agree": (agree_clean / n_agree) if n_agree else 0.0,
        "p_error_given_disagree": (disagree_error / n_disagree) if n_disagree else 0.0,
        "amterr_exact": amterr_exact,
        "amterr_total": amterr_total,
        "amterr_residual": amterr_residual,
    }


if __name__ == "__main__":
    argparse.ArgumentParser().parse_args()
    r = run()
    c = r["cells"]
    print("Layer B -- independent recomputation vs. the federal reviewer's finding")
    print(f"  households: {r['n']:,}   (skipped {r['skipped']:,})")
    print()
    print("                          reviewer found NO error   reviewer FOUND an error")
    print(f"  engine agrees              {c[(True, False)]:>14,}          {c[(True, True)]:>14,}")
    print(f"  engine disagrees           {c[(False, False)]:>14,}          {c[(False, True)]:>14,}")
    print()
    print(f"  agreement    -> no error found : {100*r['p_clean_given_agree']:.2f}%")
    print(f"  disagreement -> error found    : {100*r['p_error_given_disagree']:.2f}%")
    print()
    print(f"  |FSBEN - RAWBEN| == AMTERR     : {r['amterr_exact']:,}/{r['amterr_total']:,} "
          f"= {100*r['amterr_exact']/max(1,r['amterr_total']):.2f}%")
    print(f"  residual (gap - AMTERR)        : {r['amterr_residual'].most_common(6)}")
