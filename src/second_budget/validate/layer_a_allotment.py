"""Layer A -- replay the whole budget against real federal households.

For every usable household in the USDA SNAP QC public-use file, run the engine
from the household's raw components -- income, each deduction, shelter expenses,
size, the applicable caps -- and compare **four** stages against the file's own
reviewed values. Integer equality, no tolerance.

The chain never reads ``FSNETINC``. Net income is computed, not borrowed. That is
what makes this a test of the budget rather than of one subtraction.

    python -m second_budget.validate.layer_a_allotment
    python -m second_budget.validate.layer_a_allotment --sample

The residual histogram is the point, not a by-product. A flat mismatch rate says
nothing; a residual that clusters on one value is a specific rule missing from
the code, and it names itself. Every rule this engine implements beyond the
obvious was found that way -- the truncated earned income deduction, the half-up
rounding of adjusted income, the minimum benefit reaching a computed zero, and
the flat homeless shelter deduction that replaces the excess-shelter stage.
"""

from __future__ import annotations

import argparse
import collections
import csv
import gzip
import pathlib
from typing import Iterator

from ..engine.budget import compute
from .qc_adapter import STAGE_FOR_COLUMN, household, outcomes

REPO = pathlib.Path(__file__).resolve().parents[3]
QC_CSV = REPO / "data" / "qc" / "qc_pub_fy2024.csv"
SAMPLE = REPO / "tests" / "fixtures" / "qc_sample_2000.csv.gz"


def _rows(path: pathlib.Path) -> Iterator[dict]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", newline="", encoding="utf-8") as handle:
            yield from csv.DictReader(handle)
    else:
        with open(path, newline="", encoding="utf-8", errors="replace") as handle:
            yield from csv.DictReader(handle)


def run(path: pathlib.Path | None = None) -> dict:
    source = path or QC_CSV
    if not source.exists():
        raise SystemExit(
            f"{source} not found -- run: python -m second_budget.validate.fetch_qc --year 2024"
        )

    matched: collections.Counter[str] = collections.Counter()
    compared: collections.Counter[str] = collections.Counter()
    residual: collections.Counter[int] = collections.Counter()
    skipped = 0

    for row in _rows(source):
        facts = household(row)
        reviewed = outcomes(row) if facts is not None else None
        if facts is None or reviewed is None:
            skipped += 1
            continue

        result = compute(facts)
        for column, stage_name in STAGE_FOR_COLUMN.items():
            compared[stage_name] += 1
            if round(result.stage(stage_name).value, 2) == round(reviewed[column], 2):
                matched[stage_name] += 1

        residual[int(result.allotment - reviewed["FSBEN"])] += 1

    return {
        "source": source,
        "households": compared["allotment"],
        "skipped": skipped,
        "matched": matched,
        "compared": compared,
        "residual": residual,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sample",
        action="store_true",
        help="use the committed 2,000-household sample instead of the full file",
    )
    args = parser.parse_args()

    report = run(SAMPLE if args.sample else None)

    print("Layer A -- full budget replay, FY2024 SNAP QC public-use file")
    print(f"  source     : {report['source'].name}")
    print(f"  households : {report['households']:,}   (skipped {report['skipped']:,})")
    print()
    for stage in ("excess_shelter_deduction", "total_deductions", "net_income", "allotment"):
        n, ok = report["compared"][stage], report["matched"][stage]
        print(f"  {stage:26s} {ok:7,}/{n:,} = {100 * ok / n:7.3f}%")
    print()

    residual = report["residual"]
    off = sum(count for delta, count in residual.items() if delta != 0)
    if off:
        print(f"  allotment residual ({off:,} households not exact):")
        for delta, count in residual.most_common(8):
            if delta:
                print(f"    {delta:+6d} : {count:,}")
    else:
        print("  allotment residual : EMPTY")


if __name__ == "__main__":
    main()
