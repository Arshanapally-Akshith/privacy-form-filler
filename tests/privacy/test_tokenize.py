"""Session-scoped tokenization tests (BUILD.md Phase 3, tasks 2, 5, 6).

Written before implementation where the contract is fully specified by ARCHITECTURE.md §5
and this commit's approved plan (CLAUDE.md §4): determinism within a session, session
isolation, exact reversal, and format preservation (alphabet + length) are the "written
before implementation" list BUILD.md names for this phase. NIST vector conformance for the
underlying algorithm itself lives in test_ff1.py -- session keys are randomly generated
internally by design (that randomness *is* the isolation mechanism), so a fixed-key NIST
vector cannot be exercised through this session-level API without undermining that design.

Exhaustive enumeration over small synthetic domains lives in test_ff1.py, not here --
tokenize.py's own job is to *reject* any domain below NIST's minimum size (see the
DomainTooSmallError tests below), so a small domain cannot be exhaustively exercised
through this layer's API by design.
"""

import pytest

from app.privacy.detection import EntityType
from app.privacy.tokenize import (
    DomainTooSmallError,
    SessionPseudonymMap,
    UnknownSessionError,
    UnsupportedEntityTypeError,
    reverse_entity,
    reverse_token,
    tokenize_entity,
    tokenize_value,
)

_DIGITS = "0123456789"


def _unique_session(name: str) -> str:
    # Mirrors the codebase's existing convention (unique case_id per test against a
    # process-global registry) -- see tests/api/test_debug_retrieve.py.
    return f"tokenize-test-{name}"


# --- Format preservation ------------------------------------------------------------------


def test_output_contains_only_valid_alphabet_symbols() -> None:
    session_id = _unique_session("alphabet-symbols")
    token = tokenize_value(session_id, _DIGITS, "12345678")
    assert all(ch in _DIGITS for ch in token)


def test_output_length_identical_to_input_length() -> None:
    session_id = _unique_session("length-preserved")
    for length in (8, 9, 12, 18):
        value = "1" * length
        token = tokenize_value(session_id, _DIGITS, value)
        assert len(token) == length


# --- Determinism and session isolation -----------------------------------------------------


def test_determinism_within_a_session() -> None:
    session_id = _unique_session("determinism")
    value = "123456789012"
    assert tokenize_value(session_id, _DIGITS, value) == tokenize_value(session_id, _DIGITS, value)


def test_session_isolation_same_value_different_sessions() -> None:
    value = "123456789012"
    token_a = tokenize_value(_unique_session("isolation-a"), _DIGITS, value)
    token_b = tokenize_value(_unique_session("isolation-b"), _DIGITS, value)
    assert token_a != token_b


# --- Exact reversal -------------------------------------------------------------------------


def test_exact_encrypt_then_decrypt_round_trip() -> None:
    session_id = _unique_session("round-trip")
    value = "987654321098"
    token = tokenize_value(session_id, _DIGITS, value)
    assert reverse_token(session_id, _DIGITS, token) == value


# --- Failure modes --------------------------------------------------------------------------


def test_reversing_under_unknown_session_raises() -> None:
    with pytest.raises(UnknownSessionError):
        reverse_token(_unique_session("never-created"), _DIGITS, "1234567890")


def test_value_below_minimum_domain_size_raises() -> None:
    # radix 10, length 5 -> domain 100_000 < 1_000_000 minimum
    with pytest.raises(DomainTooSmallError):
        tokenize_value(_unique_session("too-small"), _DIGITS, "12345")


def test_value_with_out_of_alphabet_character_raises() -> None:
    with pytest.raises(ValueError):
        tokenize_value(_unique_session("bad-char"), _DIGITS, "12345678X0")


def test_empty_value_raises() -> None:
    with pytest.raises(ValueError):
        tokenize_value(_unique_session("empty"), _DIGITS, "")


# --- Entity-type convenience layer -----------------------------------------------------------


@pytest.mark.parametrize(
    ("entity_type", "value"),
    [
        (EntityType.AADHAAR, "123456789012"),
        (EntityType.PHONE, "9876543210"),
        (EntityType.PIN_CODE, "110001"),
        (EntityType.ACCOUNT_NUMBER, "123456789012345"),
        (EntityType.PAN, "ABCDE1234F"),
        (EntityType.PASSPORT, "A1234567"),
    ],
)
def test_entity_tokenize_and_reverse_round_trip_with_format_preserved(
    entity_type: EntityType, value: str
) -> None:
    session_id = _unique_session(f"entity-{entity_type.value}")
    token = tokenize_entity(session_id, entity_type, value)

    assert len(token) == len(value)
    assert token != value  # sanity: it was actually transformed
    assert reverse_entity(session_id, entity_type, token) == value


@pytest.mark.parametrize("entity_type", [EntityType.NAME, EntityType.ADDRESS, EntityType.EMAIL, EntityType.DATE])
def test_entity_types_without_a_defined_alphabet_are_rejected(entity_type: EntityType) -> None:
    with pytest.raises(UnsupportedEntityTypeError):
        tokenize_entity(_unique_session("unsupported"), entity_type, "irrelevant")


# --- SessionPseudonymMap direct unit tests ---------------------------------------------------


def test_get_or_create_key_is_stable_and_random_per_session() -> None:
    pseudonym_map = SessionPseudonymMap()
    key_a1 = pseudonym_map.get_or_create_key("session-a")
    key_a2 = pseudonym_map.get_or_create_key("session-a")
    key_b = pseudonym_map.get_or_create_key("session-b")

    assert key_a1 == key_a2
    assert key_a1 != key_b


def test_get_key_raises_for_unknown_session() -> None:
    pseudonym_map = SessionPseudonymMap()
    with pytest.raises(UnknownSessionError):
        pseudonym_map.get_key("never-created")
