"""Derive action tests (BUILD.md Phase 3, task 4).

Written before implementation is complete, per BUILD.md's explicit instruction: "Derive
correctness against the pinned dataset version; unknown PIN handling is explicit." Tested
directly against the real committed dataset (matching detection.py's existing precedent),
using examples verified precisely against app/config/data/pincode_district_state.csv before
writing this file -- not assumed or approximated.
"""

import pytest

from app.privacy.derive import (
    AmbiguousPinCodeError,
    DerivationError,
    InvalidPinCodeFormatError,
    UnknownPinCodeError,
    derive_district,
    derive_state,
)

# Verified real examples:
# "110001" -> single unambiguous entry (NEW DELHI, DELHI)
# "110007" -> 3 distinct districts (CENTRAL/NORTH/NORTH WEST), all state DELHI
# "110025" -> state itself contradictory: (SOUTH, DELHI), (South East, DELHI), (BUDAUN, UTTAR PRADESH)
# "999999" -> absent entirely
# "121999" -> present in the raw file only via NA/NA rows -> unknown after filtering


# --- Clean, unambiguous case ------------------------------------------------------------------


def test_derive_state_for_unambiguous_pincode() -> None:
    assert derive_state("110001") == "DELHI"


def test_derive_district_for_unambiguous_pincode() -> None:
    assert derive_district("110001") == "NEW DELHI"


# --- District-ambiguous, state-consistent: state still derivable, district is not -------------


def test_derive_state_succeeds_when_only_district_is_ambiguous() -> None:
    assert derive_state("110007") == "DELHI"


def test_derive_district_raises_when_district_is_ambiguous() -> None:
    with pytest.raises(AmbiguousPinCodeError):
        derive_district("110007")


# --- State-ambiguous: both attributes fail -------------------------------------------------------


def test_derive_state_raises_when_state_itself_is_ambiguous() -> None:
    with pytest.raises(AmbiguousPinCodeError):
        derive_state("110025")


def test_derive_district_raises_when_state_itself_is_ambiguous() -> None:
    with pytest.raises(AmbiguousPinCodeError):
        derive_district("110025")


# --- Unknown PIN, including the NA-only edge case ------------------------------------------------


def test_derive_state_raises_for_genuinely_absent_pincode() -> None:
    with pytest.raises(UnknownPinCodeError):
        derive_state("999999")


def test_derive_district_raises_for_genuinely_absent_pincode() -> None:
    with pytest.raises(UnknownPinCodeError):
        derive_district("999999")


def test_derive_state_raises_for_pincode_present_only_via_na_rows() -> None:
    with pytest.raises(UnknownPinCodeError):
        derive_state("121999")


def test_derive_district_raises_for_pincode_present_only_via_na_rows() -> None:
    with pytest.raises(UnknownPinCodeError):
        derive_district("121999")


# --- Malformed input, checked before any lookup ---------------------------------------------------


@pytest.mark.parametrize("bad_input", ["1234", "1234567", "ABCDEF", "", "11000A", "110 001"])
def test_derive_state_raises_for_malformed_input(bad_input: str) -> None:
    with pytest.raises(InvalidPinCodeFormatError):
        derive_state(bad_input)


@pytest.mark.parametrize("bad_input", ["1234", "1234567", "ABCDEF", "", "11000A", "110 001"])
def test_derive_district_raises_for_malformed_input(bad_input: str) -> None:
    with pytest.raises(InvalidPinCodeFormatError):
        derive_district(bad_input)


# --- Exception hierarchy ----------------------------------------------------------------------------


def test_exception_hierarchy_shares_a_common_derivation_error_base() -> None:
    assert issubclass(InvalidPinCodeFormatError, DerivationError)
    assert issubclass(UnknownPinCodeError, DerivationError)
    assert issubclass(AmbiguousPinCodeError, DerivationError)
