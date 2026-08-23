"""Layer C -- prove the transcribed constants against the file's own columns.

The constants table is hand-transcribed from a published PDF. That is the right
direction (deriving it from the data and then checking it against the same data
would prove nothing), but hand transcription has one characteristic failure: a
typo produces a plausible budget that is silently wrong for every household in
that band. Which is precisely the sin this project exists to attack.

So every value is checked, per household, against the microdata's own columns:

    BENMAX       maximum allotment for this unit's size and region
    FSSTDDED     standard deduction
    MINIMUM_BEN  minimum benefit amount
    SHELCAP      maximum allowable shelter deduction

Exact equality, no tolerance. This is the distance-of-known-length discipline
applied to a lookup table: before trusting a table, check it against something
that was measured independently.

    python -m second_budget.validate.layer_c_constants

If a value disagrees, the table is corrected or the region is dropped from
coverage. It is never adjusted to fit -- a table tuned to the data it is checked
against has stopped being evidence.
"""

from __future__ import annotations

import argparse
import collections
import csv
import gzip
import pathlib
from typing import Iterator

from ..engine.constants import OutOfCoverage, for_state, region_for_state
from .qc_adapter import number

REPO = pathlib.Path(__file__).resolve().parents[3]
QC_CSV = REPO / "data" / "qc" / "qc_pub_fy2024.csv"
SAMPLE = REPO / "tests" / "fixtures" / "qc_sample_2000.csv.gz"

#: column -> (human name, how to get it from a Constants object)
CHECKS = {
    "BENMAX": ("maximum allotment", lambda c, row: c.max_allotment(int(row["size"]))),
    "FSSTDDED": ("standard deduction", lambda c, row: c.standard_deduction(int(row["size"]))),
    "MINIMUM_BEN": ("minimum benefit", lambda c, row: c.minimum_benefit()),
    "SHELCAP": ("excess shelter cap", lambda c, row: c._shelter_cap),
}


def _size_matching(constants, get, recorded: float, *, up_to: int = 12) -> int | None:
    """The household size at which the table would produce ``recorded``, if any."""
    for size in range(1, up_to + 1):
        try:
            if float(get(constants, {"size": size})) == recorded:
                return size
        except Exception:  # noqa: BLE001 - a size the accessor cannot serve
            continue
    return None


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

    checked: collections.Counter[str] = collections.Counter()
    matched: collections.Counter[str] = collections.Counter()
    disagreements: dict[str, collections.Counter] = {c: collections.Counter() for c in CHECKS}
    refused = 0
    skipped = 0

    for row in _rows(source):
        state = row.get("STATENAME", "").strip()
        size = number(row.get("CERTHHSZ"))
        if size is None or size < 1:
            skipped += 1
            continue

        try:
            constants = for_state(state)
        except OutOfCoverage:
            refused += 1
            continue
        if not constants.covered:
            refused += 1
            continue

        for column, (_name, get) in CHECKS.items():
            recorded = number(row.get(column))
            if recorded is None:
                continue
            checked[column] += 1
            expected = get(constants, {"size": size})
            if float(expected) == float(recorded):
                matched[column] += 1
            else:
                # Before calling this a table error, ask whether the recorded
                # value is the table's own figure for a *different* household
                # size. If it is, the disagreement is between two columns of the
                # file, not between the file and the table.
                other = _size_matching(constants, get, float(recorded))
                disagreements[column][(int(size), float(expected), float(recorded), other)] += 1

    return {
        "source": source,
        "checked": checked,
        "matched": matched,
        "disagreements": disagreements,
        "refused": refused,
        "skipped": skipped,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", action="store_true",
                        help="use the committed 2,000-household sample")
    args = parser.parse_args()

    report = run(SAMPLE if args.sample else None)

    print("Layer C -- transcribed constants vs. the file's own columns")
    print(f"  source  : {report['source'].name}")
    print(f"  refused : {report['refused']:,} households outside coverage "
          f"(separate benefit schedules)")
    print()
    unexplained = 0
    row_anomalies = 0
    for column, (name, _get) in CHECKS.items():
        n, hit = report["checked"][column], report["matched"][column]
        if not n:
            continue
        print(f"  {name:22s} ({column:12s}) {hit:7,}/{n:,} = {100 * hit / n:7.3f}%")
        for (size, expected, recorded, other), count in report["disagreements"][column].most_common(8):
            if other is not None:
                row_anomalies += count
                print(f"      size {size}: file says {recorded:g}, which is this table's "
                      f"value for size {other} ({count:,} household"
                      f"{'s' if count != 1 else ''})")
            else:
                unexplained += count
                print(f"      size {size}: table says {expected:g}, file says {recorded:g} "
                      f"-- UNEXPLAINED ({count:,} households)")
    print()
    if unexplained:
        print(f"  {unexplained:,} UNEXPLAINED disagreements -- fix the table or drop the region")
    else:
        print("  ALL CONSTANTS PROVEN against the file's own columns.")
        if row_anomalies:
            print(f"  {row_anomalies} household(s) carry a constant belonging to a different")
            print("  size than their own CERTHHSZ. That is a disagreement between two columns")
            print("  of the microdata, not between the microdata and this table. Reported,")
            print("  not smoothed.")


if __name__ == "__main__":
    main()
