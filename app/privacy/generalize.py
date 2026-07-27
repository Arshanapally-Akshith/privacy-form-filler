"""Generalize action: DOB -> age band (BUILD.md Phase 3, task 3).

Age is computed against a caller-supplied reference date, never date.today() and never any
implicit global -- ARCHITECTURE.md's pinned reference date is "the form's creation/
submission date" (DECISIONS.md E1), a piece of data that does not yet exist anywhere in
this codebase (app.api.case_store.CaseRecord has no timestamp field; wiring one is Phase 4/5
work, not this commit's). Making reference_date an explicit parameter is also what
CLAUDE.md §4's "no reliance on wall-clock time" and §6's "one function, one call site for
the age computation" require: a hidden internal clock read would make this both untestable
without monkeypatching time and secretly stateful rather than pure.

Layered, composable design, mirroring app.privacy.tokenize's generic-primitive-plus-
convenience-wrapper pattern:
- parse_date_of_birth: string -> date, no reference date needed.
- compute_age: the one canonical age function CLAUDE.md §6 requires -- also owns
  plausibility validation (future DOB, implausible age), since that check inherently needs
  both dates together.
- age_band: pure, trivial band labeling.
- generalize_dob: composes the three above; the actual Generalize action.

Not reversible, unlike Tokenize (ARCHITECTURE.md §5.1's action table marks Generalize
"No") -- there is deliberately no reverse_* function here. A coarsened age band cannot and
should not map back to an exact DOB.

Purely deterministic date arithmetic throughout -- no probabilistic inference, per
ARCHITECTURE.md §5.1's constraint on Generalize/Derive.
"""

from datetime import date, datetime

from app.privacy.constants import (
    AGE_BAND_WIDTH_YEARS,
    DATE_OF_BIRTH_FORMATS,
    LEAP_DAY_FALLBACK_MONTH_DAY,
    MAX_PLAUSIBLE_AGE_YEARS,
)


class GeneralizationError(ValueError):
    """Base class for Generalize-action failures."""


class DateParseError(GeneralizationError):
    """Raised when a DOB string matches none of the supported formats, or matches a format
    but describes a calendar-impossible date (e.g. "31/02/1990")."""


class ImplausibleDateOfBirthError(GeneralizationError):
    """Raised when a DOB parses fine but produces a logically impossible or implausible
    age relative to the reference date (in the future, or unrealistically old)."""


def parse_date_of_birth(value: str) -> date:
    for fmt in DATE_OF_BIRTH_FORMATS:
        try:
            # A date of birth has no meaningful time-of-day or timezone component; .date()
            # discards whatever naive placeholder strptime attaches immediately below.
            return datetime.strptime(value, fmt).date()  # noqa: DTZ007
        except ValueError:
            continue
    raise DateParseError(f"Could not parse {value!r} as a date of birth using any supported format")


def compute_age(dob: date, reference_date: date) -> int:
    try:
        birthday_this_year = dob.replace(year=reference_date.year)
    except ValueError:
        # dob is Feb 29 and reference_date.year is not a leap year -- documented
        # convention (LEAP_DAY_FALLBACK_MONTH_DAY): treat the birthday as Feb 28.
        month, day = LEAP_DAY_FALLBACK_MONTH_DAY
        birthday_this_year = date(reference_date.year, month, day)

    age = reference_date.year - dob.year
    if reference_date < birthday_this_year:
        # Birthday hasn't happened yet this year. Strict "<", not "<=": if reference_date
        # falls exactly on the birthday, the birthday has already occurred -- the person is
        # counted as having just turned their new age, not their previous one.
        age -= 1

    if age < 0 or age > MAX_PLAUSIBLE_AGE_YEARS:
        raise ImplausibleDateOfBirthError(
            f"Computed age {age} for DOB {dob.isoformat()} against reference date "
            f"{reference_date.isoformat()} is not plausible"
        )
    return age


def age_band(age: int) -> str:
    band_start = (age // AGE_BAND_WIDTH_YEARS) * AGE_BAND_WIDTH_YEARS
    band_end = band_start + AGE_BAND_WIDTH_YEARS - 1
    return f"{band_start}-{band_end}"


def generalize_dob(value: str, reference_date: date) -> str:
    dob = parse_date_of_birth(value)
    age = compute_age(dob, reference_date)
    return age_band(age)
