# Second Budget

An independent second opinion on a household's SNAP determination.

A benefits navigator sits with a household holding a notice of action. The
household asks the only question that matters: *is this number right?* Today the
honest answer is that nobody checks. Second Budget re-derives the allotment from
the underlying facts, and when it disagrees it says **which stage** of the budget
it disagrees with and by how much, with the regulation quoted verbatim.

**Status: in progress.** The calculation engine and its validation against
federal microdata are complete and measured. The agent orchestration is being
built. Numbers below are reproducible from this repository.

## What is proven, not asserted

The engine is validated by exact replay against the USDA SNAP Quality Control
public-use microdata for FY2024 — 44,891 real, de-identified households that
carry both the inputs to the budget and the benefit that resulted.

**Allotment replay: 42,689 of 42,689 exact. 100.000%. Residual histogram empty.**

Integer equality, no tolerance. Reproduce it:

```bash
python -m second_budget.validate.fetch_qc --year 2024
python -m second_budget.validate.layer_a_allotment
```

### The two rules the microdata found

A naive implementation of the allotment formula scores 91.287%. The failures are
not noise — they are *shaped*, and the shape names the missing rule:

1. **−23 on 3,259 households.** The minimum benefit. Measured, not assumed: it
   applies only to household sizes 1 and 2, and it applies **even when the
   computed allotment is zero**, not merely when it is small. Of the affected
   households, every one has a computed allotment below the floor and none has
   one at or above it.
2. **The rounding sits before the subtraction.** 7 CFR 273.10(e)(2)(ii)(C)
   requires the household's 30% share to be rounded *up to the next whole dollar
   and then subtracted*, not the result rounded afterwards. The two readings
   differ by a dollar on a large share of real households, always in the same
   direction.

Neither rule was found by reading the regulation more carefully. Both were found
by demanding an empty residual histogram against real records.

### Does disagreement mean anything?

Measured over 43,299 households, comparing an independent recomputation against
the benefit actually received (`RAWBEN`) and against the federal reviewer's own
error findings:

|                      | reviewer found no error | reviewer found an error |
| -------------------- | ----------------------: | ----------------------: |
| **engine agrees**    |                  23,596 |                     204 |
| **engine disagrees** |                   2,585 |                  16,914 |

- Agreement → the reviewer found nothing in **99.14%** of cases.
- Disagreement → the reviewer found something in **86.74%** of cases.

```bash
python -m second_budget.validate.layer_b_localisation
```

**What this does not claim.** Agreement is measured; causation is not. A
reviewer's finding can rest on facts this file does not expose, and a
disagreement can equally be the engine's own limitation. Both error cells are
published above rather than summarised away.

## Running the tests

The full suite runs on a committed 2,000-household sample with **no download, no
AWS account, and no credentials of any kind**:

```bash
pip install -e ".[dev]"
pytest -q
```

The agent loop is exercised by a real Strands `Model` implementation
(`models/scripted.py`) that replays a script rather than calling a provider — so
graph topology, interventions, interrupts and the exact model-call count are all
assertable offline.

## Coverage, and what it refuses

FY2024, and the 49 jurisdictions on the contiguous benefit schedule. Alaska,
Hawaii, Guam and the Virgin Islands run separate maximum-allotment and
minimum-benefit tables; the engine refuses for them rather than guessing.
Refusal is a shipped feature with its own tests, not an unhandled case.

## Data

| Source | URL |
| --- | --- |
| SNAP QC public-use microdata FY2024 | <https://snapqcdata.net/datafiles> |
| FY2024 technical documentation / codebook | `FY-2024-Tech-Doc.pdf`, same site |

No registration and no data-use agreement. `data/qc_manifest.json` records the
SHA256 of the archive this repository's numbers were measured against, so a
reader can prove they hold the same bytes.

Variable meanings are taken from the published codebook, not inferred. The
distinction that matters most: `FSBEN` is *constructed* ("Final calculated
benefit" — the reviewed computation) while `RAWBEN` is *reported* ("Reported
SNAP benefit received"). Layer A measures agreement with the reviewed
computation. It does not claim agreement with what a state issued.

## Licence

Apache-2.0. Not legal advice; output is a draft for a human advocate to review.
