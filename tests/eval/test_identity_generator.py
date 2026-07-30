"""Tests for the Phase 6 identity generator (BUILD.md Phase 6 tasks 1-2). Pure-Python,
seeded-RNG generation -- no I/O, no LLM, offline per CLAUDE.md §4 by construction.
"""

import random
import re
from datetime import date

import pytest

from app.privacy.derive import (
    AmbiguousPinCodeError,
    UnknownPinCodeError,
    derive_district,
    derive_state,
)
from app.privacy.generalize import compute_age
from eval.dataset.identity_generator import (
    CITY_POOL,
    MAX_AGE_YEARS,
    MIN_AGE_YEARS,
    generate_identity,
)

REFERENCE_DATE = date(2026, 7, 29)

_PAN_FORMAT = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_AADHAAR_FORMAT = re.compile(r"^\d{12}$")
_PHONE_FORMAT = re.compile(r"^[6-9]\d{9}$")
_PIN_FORMAT = re.compile(r"^\d{6}$")
_ACCOUNT_FORMAT = re.compile(r"^\d{9,18}$")


def test_same_seed_produces_identical_identity() -> None:
    identity_a = generate_identity(random.Random(42), REFERENCE_DATE)
    identity_b = generate_identity(random.Random(42), REFERENCE_DATE)

    assert identity_a == identity_b


def test_different_seeds_diverge() -> None:
    identity_a = generate_identity(random.Random(1), REFERENCE_DATE)
    identity_b = generate_identity(random.Random(2), REFERENCE_DATE)

    assert identity_a != identity_b


@pytest.mark.parametrize("seed", range(200))
def test_pin_code_drawn_from_constrained_city_pool(seed: int) -> None:
    identity = generate_identity(random.Random(seed), REFERENCE_DATE)
    pool_by_pincode = {entry.pincode: entry for entry in CITY_POOL}

    assert identity.pin_code in pool_by_pincode
    matched = pool_by_pincode[identity.pin_code]
    assert identity.city == matched.city
    assert identity.state == matched.state


def test_city_pool_pincodes_resolve_unambiguously_via_derive() -> None:
    """Guards the module docstring's claim directly: every CITY_POOL entry must resolve to
    exactly one (district, state) pair in the committed dataset, or Phase 7's k-anonymity
    grouping and Derive ground truth silently break for that city."""
    for entry in CITY_POOL:
        try:
            derive_state(entry.pincode)
            derive_district(entry.pincode)
        except (UnknownPinCodeError, AmbiguousPinCodeError) as exc:
            pytest.fail(f"CITY_POOL entry {entry} does not resolve unambiguously: {exc}")


@pytest.mark.parametrize("seed", range(200))
def test_age_falls_within_the_realistic_bound(seed: int) -> None:
    identity = generate_identity(random.Random(seed), REFERENCE_DATE)
    age = compute_age(identity.date_of_birth, REFERENCE_DATE)

    assert MIN_AGE_YEARS - 1 <= age <= MAX_AGE_YEARS


@pytest.mark.parametrize("seed", range(50))
def test_identifier_formats_match_the_form_schemas_expected_format(seed: int) -> None:
    identity = generate_identity(random.Random(seed), REFERENCE_DATE)

    assert _PAN_FORMAT.match(identity.pan_number)
    assert _AADHAAR_FORMAT.match(identity.aadhaar_number)
    assert _PHONE_FORMAT.match(identity.phone_number)
    assert _PIN_FORMAT.match(identity.pin_code)
    assert _ACCOUNT_FORMAT.match(identity.linked_account_number)
    assert "@" in identity.email_address


def test_residential_address_contains_city_and_state() -> None:
    identity = generate_identity(random.Random(7), REFERENCE_DATE)

    assert identity.city in identity.residential_address
    assert identity.state in identity.residential_address


def test_annual_income_is_positive_and_bounded() -> None:
    identity = generate_identity(random.Random(3), REFERENCE_DATE)

    assert 0 < identity.annual_income <= 2_500_000
