"""Layer A -- prove the allotment stage by exact replay against federal records.

For every usable household in the USDA SNAP QC public-use file, recompute the
allotment from the household's own recorded net income, maximum allotment and
size, and require **exact integer equality** with the benefit the state actually
issued. No tolerance, no fuzzy match.

The residual histogram is the point of this file, not a by-product. A flat
mismatch rate says nothing. A residual that clusters on one value is a specific
rule stated in the regulation and missing from the code, and it names itself.
"""
from __future__ import annotations

import argparse
import collections
import csv
import pathlib

from ..engine.allotment import allotment

REPO = pathlib.Path(__file__).resolve().parents[3]
QC_CSV = REPO / "data" / "qc" / "qc_pub_fy2024.csv"

# FY2024, 48 contiguous states + DC. Both values are proven against the file's
# own columns by Layer C rather than trusted here.
CONTIGUOUS_MAX_ALLOTMENT_SIZE1 = 291
MINIMUM_BENEFIT = 23


def _num(v: str | None) -> float | None:
    v = (v or "").strip()
    if v in ("", ".", "NA"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def run(limit: int | None = None, contiguous_only: bool = True) -> dict:
    exact = mismatch = skipped = 0
    residual: collections.Counter[int] = collections.Counter()

    with open(QC_CSV, newline="", encoding="utf-8", errors="replace") as f:
        for i, row in enumerate(csv.DictReader(f)):
            if limit is not None and i >= limit:
                break
            benmax = _num(row["BENMAX"])
            net = _num(row["FSNETINC"])
            issued = _num(row["FSBEN"])
            size = _num(row["CERTHHSZ"])
            if None in (benmax, net, issued, size):
                skipped += 1
                continue
            # Scope: the 49 jurisdictions on the contiguous benefit schedule.
            # Alaska, Hawaii, Guam and the Virgin Islands run their own maximum
            # allotment and minimum benefit tables and are out of coverage.
            if contiguous_only and not _is_contiguous(row, benmax, size):
                skipped += 1
                continue

            got = allotment(
                max_allotment=int(benmax),
                net_income=net,
                household_size=int(size),
                minimum_benefit=MINIMUM_BENEFIT,
            ).allotment
            if got == int(issued):
                exact += 1
            else:
                mismatch += 1
                residual[got - int(issued)] += 1

    total = exact + mismatch
    return {
        "total": total,
        "exact": exact,
        "mismatch": mismatch,
        "skipped": skipped,
        "pct": (100.0 * exact / total) if total else 0.0,
        "residual": residual,
    }


_CONTIGUOUS_SIZE1_MAX = {291}


def _is_contiguous(row: dict, benmax: float, size: float) -> bool:
    """Alaska/Hawaii/Guam/VI run separate tables; exclude them by name."""
    return row.get("STATENAME", "").strip() not in {
        "Alaska", "Hawaii", "Guam", "Virgin Islands",
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    r = run(a.limit)
    print(f"Layer A -- allotment replay, FY2024 QC public-use file")
    print(f"  households compared : {r['total']:,}")
    print(f"  exact matches       : {r['exact']:,}  ({r['pct']:.3f}%)")
    print(f"  mismatches          : {r['mismatch']:,}")
    print(f"  skipped             : {r['skipped']:,}")
    if r["residual"]:
        print("  residual histogram (computed - issued):")
        for delta, n in r["residual"].most_common(10):
            print(f"    {delta:+5d} : {n:,}")
    else:
        print("  residual histogram  : EMPTY")
