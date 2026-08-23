"""Resolve the regulatory constants a budget needs -- or refuse and say why.

The maximum allotment, the standard deduction, the minimum benefit and the
excess-shelter cap are all set annually and vary by region. They are not
computed; they are looked up. So the only interesting question about this module
is what it does when it *cannot* look something up.

It refuses, and it names the missing constant and where the value would come
from. Refusal is a shipped feature with its own tests, because the alternative --
falling back to the contiguous-US figure for an Alaskan household -- produces an
answer that is plausible, wrong, and indistinguishable from a right one. That is
the exact failure this project exists to attack, and committing it inside the
tool would be worse than not building the tool.
"""

from __future__ import annotations

import functools
import pathlib
from dataclasses import dataclass

import yaml

DATA = pathlib.Path(__file__).resolve().parents[3] / "data" / "constants"

#: States that do not use the contiguous schedule as published. Alaska, Hawaii,
#: Guam and the Virgin Islands have their own tables in Appendix F. Illinois is
#: here for a different reason -- see the note on its entry in the YAML.
_SEPARATE_SCHEDULE = {
    "Illinois": "illinois",
    "Alaska": "alaska_urban",
    "Hawaii": "hawaii",
    "Guam": "guam",
    "Virgin Islands": "virgin_islands",
}


class OutOfCoverage(Exception):
    """A constant is needed that this build cannot supply for this household.

    Carries the specific reason and the source, so the refusal a navigator sees
    says what is missing rather than "unsupported".
    """

    def __init__(self, *, constant: str, region: str, because: str, source_url: str) -> None:
        super().__init__(
            f"{constant} is not available for {region}: {because}. "
            f"The published schedule is at {source_url}"
        )
        self.constant = constant
        self.region = region
        self.because = because
        self.source_url = source_url


@dataclass(frozen=True)
class Constants:
    """One region's schedule for one fiscal year."""

    fiscal_year: int
    region: str
    label: str
    covered: bool
    uncovered_because: str
    source_url: str
    _max_allotment: dict[int, int]
    _additional_person: int
    _minimum_benefit: int
    _standard_deduction: dict[int, int]
    _shelter_cap: int
    homeless_shelter_deduction: float
    medical_expense_threshold: float

    def _require_coverage(self, constant: str) -> None:
        if not self.covered:
            raise OutOfCoverage(
                constant=constant,
                region=self.label,
                because=self.uncovered_because,
                source_url=self.source_url,
            )

    def max_allotment(self, household_size: int) -> int:
        """Table F.5. Sizes above eight add a fixed amount per extra person."""
        self._require_coverage("maximum allotment")
        if household_size < 1:
            raise ValueError(f"household size must be at least 1, got {household_size}")
        largest = max(self._max_allotment)
        if household_size <= largest:
            return self._max_allotment[household_size]
        return self._max_allotment[largest] + self._additional_person * (household_size - largest)

    def standard_deduction(self, household_size: int) -> int:
        """Table F.3. The schedule bands sizes; six and above share one value."""
        self._require_coverage("standard deduction")
        if household_size < 1:
            raise ValueError(f"household size must be at least 1, got {household_size}")
        return self._standard_deduction[min(household_size, max(self._standard_deduction))]

    def minimum_benefit(self) -> int:
        """Table F.6: the region's minimum benefit amount.

        This is the *constant*, not the rule. Whether it applies to a given
        household -- one- and two-person units only -- is a separate question
        that belongs with the allotment calculation, and conflating the two here
        was a real bug: the microdata records MINIMUM_BEN for households of every
        size, and returning 0 for larger ones disagreed with the file on 10,617
        of them.
        """
        self._require_coverage("minimum benefit")
        return self._minimum_benefit

    def shelter_cap(self, *, has_elderly_or_disabled_member: bool) -> float | None:
        """Table F.3. ``None`` means no cap applies, which is not the same as zero."""
        self._require_coverage("excess shelter cap")
        return None if has_elderly_or_disabled_member else float(self._shelter_cap)


@functools.lru_cache(maxsize=None)
def _schedule(fiscal_year: int) -> dict:
    path = DATA / f"fy{fiscal_year}.yaml"
    if not path.exists():
        raise OutOfCoverage(
            constant="the annual parameter schedule",
            region=f"FY{fiscal_year}",
            because="no transcribed schedule exists for that fiscal year",
            source_url="https://snapqcdata.net/datafiles",
        )
    return yaml.safe_load(path.read_text(encoding="utf-8"))


#: Postal abbreviations, because an elicitor writes down whatever the household
#: said. Without this, "IL" would quietly resolve to the contiguous schedule
#: while "Illinois" is refused -- the same household getting two different
#: answers depending on how someone typed its state.
_POSTAL = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "GU": "Guam",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana",
    "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "VI": "Virgin Islands",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin",
    "WY": "Wyoming",
}
_BY_NAME = {name.casefold(): name for name in _POSTAL.values()}


class UnknownState(Exception):
    """The state on the case is not one this build recognises."""


def normalise_state(state: str) -> str:
    """Accept "IL", "il", "Illinois" or "  illinois " and return "Illinois"."""
    raw = (state or "").strip()
    if not raw:
        raise UnknownState("no state was recorded for this household")
    if len(raw) == 2 and raw.upper() in _POSTAL:
        return _POSTAL[raw.upper()]
    full = _BY_NAME.get(raw.casefold())
    if full is None:
        raise UnknownState(
            f"{state!r} is not a US state or territory this build recognises"
        )
    return full


def region_for_state(state_name: str) -> str:
    """Which benefit schedule a state runs on."""
    return _SEPARATE_SCHEDULE.get(normalise_state(state_name), "contiguous")


def for_region(region: str, *, fiscal_year: int = 2024) -> Constants:
    schedule = _schedule(fiscal_year)
    entry = schedule["regions"].get(region)
    if entry is None:
        raise OutOfCoverage(
            constant="the regional schedule",
            region=region,
            because="that region is not in the transcribed schedule",
            source_url=schedule["source"]["url"],
        )
    return Constants(
        fiscal_year=schedule["fiscal_year"],
        region=region,
        label=entry["label"],
        covered=bool(entry["covered"]),
        uncovered_because=entry.get("uncovered_because", "not covered by this build"),
        source_url=schedule["source"]["url"],
        _max_allotment={int(k): int(v) for k, v in entry["max_allotment"].items()},
        _additional_person=int(entry["additional_person"]),
        _minimum_benefit=int(entry["minimum_benefit"]),
        _standard_deduction={int(k): int(v) for k, v in entry["standard_deduction"].items()},
        _shelter_cap=int(entry["shelter_cap"]),
        homeless_shelter_deduction=float(schedule["homeless_shelter_deduction"]),
        medical_expense_threshold=float(schedule["medical_expense_threshold"]),
    )


def for_state(state_name: str, *, fiscal_year: int = 2024) -> Constants:
    return for_region(region_for_state(state_name), fiscal_year=fiscal_year)
