"""Generalize action tests (BUILD.md Phase 3, task 3).

Written before implementation is complete, per BUILD.md's explicit instruction for this
behavior: "Generalize correctness against the pinned reference date, including edge cases
(birthday on the submission date, band boundaries)." No test calls date.today() or relies
on the current date -- every test passes an explicit, fixed reference date (CLAUDE.md §4).
"""

from datetime import date

import pytest

from app.privacy.generalize import (
    DateParseError,
    GeneralizationError,
    ImplausibleDateOfBirthError,
    age_band,
    compute_age,
    generalize_dob,
    parse_date_of_birth,
)

_REFERENCE = date(2026, 7, 27)


# --- parse_date_of_birth --------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["15/06/1990", "15-06-1990", "15 June 1990", "15 Jun 1990"],
)
def test_all_supported_formats_parse_to_the_same_date(value: str) -> None:
    assert parse_date_of_birth(value) == date(1990, 6, 15)


def test_unparseable_string_raises_date_parse_error() -> None:
    with pytest.raises(DateParseError):
        parse_date_of_birth("not-a-date")


def test_calendar_invalid_date_raises_date_parse_error() -> None:
    with pytest.raises(DateParseError):
        parse_date_of_birth("31/02/1990")  # February 31 does not exist


def test_two_digit_year_raises_date_parse_error_not_silently_windowed() -> None:
    """Required adjustment: %y is removed entirely, not windowed. An ambiguous two-digit
    year must fail loudly, never guess a century."""
    with pytest.raises(DateParseError):
        parse_date_of_birth("15/06/90")


# --- compute_age: ordinary cases, birthday boundary, decade boundaries ----------------------


def test_compute_age_ordinary_case() -> None:
    assert compute_age(date(1990, 6, 15), _REFERENCE) == 36  # birthday already passed this year


def test_compute_age_birthday_not_yet_occurred_this_year() -> None:
    assert compute_age(date(1990, 8, 1), _REFERENCE) == 35  # birthday is after 27 July


@pytest.mark.parametrize("years_ago", [10, 20, 30, 40, 50, 60, 70, 80, 90])
def test_compute_age_exactly_on_birthday_equals_reference_year_minus_dob_year(years_ago: int) -> None:
    dob = date(_REFERENCE.year - years_ago, _REFERENCE.month, _REFERENCE.day)
    assert compute_age(dob, _REFERENCE) == years_ago


@pytest.mark.parametrize("years_ago", [10, 20, 30, 40, 50, 60, 70, 80, 90])
def test_compute_age_one_day_before_birthday_is_one_less(years_ago: int) -> None:
    dob = date(_REFERENCE.year - years_ago, _REFERENCE.month, _REFERENCE.day + 1)
    assert compute_age(dob, _REFERENCE) == years_ago - 1


# --- Leap-day DOB -----------------------------------------------------------------------------


def test_leap_day_dob_against_non_leap_reference_year_before_fallback_birthday() -> None:
    # Reference year 2025 is not a leap year; Feb 29 falls back to Feb 28 (documented
    # convention). 27 Feb 2025 is one day before that fallback birthday.
    dob = date(2000, 2, 29)
    reference = date(2025, 2, 27)
    assert compute_age(dob, reference) == 24


def test_leap_day_dob_against_non_leap_reference_year_on_or_after_fallback_birthday() -> None:
    dob = date(2000, 2, 29)
    reference = date(2025, 3, 1)  # on/after the Feb-28 fallback birthday
    assert compute_age(dob, reference) == 25


def test_leap_day_dob_against_leap_reference_year_exact_birthday() -> None:
    dob = date(2000, 2, 29)
    reference = date(2024, 2, 29)  # 2024 is a leap year -- the real birthday exists
    assert compute_age(dob, reference) == 24


# --- Plausibility -------------------------------------------------------------------------------


def test_future_dob_raises_implausible_date_of_birth_error() -> None:
    with pytest.raises(ImplausibleDateOfBirthError):
        compute_age(date(2027, 1, 1), _REFERENCE)


def test_implausibly_old_dob_raises_implausible_date_of_birth_error() -> None:
    with pytest.raises(ImplausibleDateOfBirthError):
        compute_age(date(1800, 1, 1), _REFERENCE)


def test_exception_hierarchy_shares_a_common_generalization_error_base() -> None:
    assert issubclass(DateParseError, GeneralizationError)
    assert issubclass(ImplausibleDateOfBirthError, GeneralizationError)


# --- age_band: decade boundaries ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("age", "expected_band"),
    [
        (0, "0-9"),
        (9, "0-9"),
        (10, "10-19"),
        (29, "20-29"),
        (30, "30-39"),
        (99, "90-99"),
        (105, "100-109"),
    ],
)
def test_age_band_decade_boundaries(age: int, expected_band: str) -> None:
    assert age_band(age) == expected_band


# --- generalize_dob: end-to-end composition -----------------------------------------------------


def test_generalize_dob_end_to_end() -> None:
    assert generalize_dob("15/06/1990", _REFERENCE) == "30-39"


def test_generalize_dob_propagates_parse_errors() -> None:
    with pytest.raises(DateParseError):
        generalize_dob("garbage", _REFERENCE)


def test_generalize_dob_propagates_plausibility_errors() -> None:
    with pytest.raises(ImplausibleDateOfBirthError):
        generalize_dob("01/01/2027", _REFERENCE)
