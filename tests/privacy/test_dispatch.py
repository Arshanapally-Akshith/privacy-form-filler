"""Action dispatch tests (BUILD.md Phase 3, completing the policy engine composition).

Exercises dispatch.py's routing over the real, already-tested primitives from Commits 1-4
-- no stubs. PIN code examples reused from tests/privacy/test_derive.py, verified there
against the real committed dataset: "110001" unambiguous, "110025" state-ambiguous,
"999999" genuinely absent.
"""

from datetime import date

import pytest

from app.privacy.detection import DetectedEntity, EntityType
from app.privacy.dispatch import (
    DeriveAttribute,
    MissingDispatchParameterError,
    PolicyAction,
    UnsupportedActionForEntityTypeError,
    apply_action,
    resolve_action,
)
from app.privacy.generalize import generalize_dob
from app.privacy.tokenize import reverse_entity


def _entity(entity_type: EntityType, text: str) -> DetectedEntity:
    return DetectedEntity(entity_type=entity_type, text=text, start=0, end=len(text))


# --- resolve_action: fail-closed default (I4/E5/P10) ------------------------------------


def test_resolve_action_defaults_to_tokenize_when_no_explicit_rule() -> None:
    assert resolve_action(None) is PolicyAction.TOKENIZE


def test_resolve_action_returns_explicit_action_unchanged() -> None:
    assert resolve_action(PolicyAction.PASS_THROUGH) is PolicyAction.PASS_THROUGH


# --- Pass-through -------------------------------------------------------------------------


def test_pass_through_returns_text_unchanged() -> None:
    entity = _entity(EntityType.NAME, "Asha Rao")
    assert apply_action(PolicyAction.PASS_THROUGH, entity, session_id="s1") == "Asha Rao"


# --- Tokenize: supported entity types, with round-trip proof -----------------------------


@pytest.mark.parametrize(
    ("entity_type", "canonical_value"),
    [
        (EntityType.AADHAAR, "123456789012"),
        (EntityType.PHONE, "9876543210"),
        (EntityType.PIN_CODE, "110001"),
        (EntityType.ACCOUNT_NUMBER, "123456789012345"),
        (EntityType.PAN, "ABCDE1234F"),
        (EntityType.PASSPORT, "A1234567"),
    ],
)
def test_tokenize_round_trips_for_each_supported_entity_type(
    entity_type: EntityType, canonical_value: str
) -> None:
    entity = _entity(entity_type, canonical_value)
    token = apply_action(PolicyAction.TOKENIZE, entity, session_id="s1")

    assert token != canonical_value
    assert reverse_entity("s1", entity_type, token) == canonical_value


def test_tokenize_canonicalizes_phone_with_country_code_prefix() -> None:
    entity = _entity(EntityType.PHONE, "+91 98765 43210")
    token = apply_action(PolicyAction.TOKENIZE, entity, session_id="s1")

    assert reverse_entity("s1", EntityType.PHONE, token) == "9876543210"


def test_tokenize_canonicalizes_aadhaar_with_separators() -> None:
    entity = _entity(EntityType.AADHAAR, "1234 5678-9012")
    token = apply_action(PolicyAction.TOKENIZE, entity, session_id="s1")

    assert reverse_entity("s1", EntityType.AADHAAR, token) == "123456789012"


def test_tokenize_uppercases_lowercase_pan() -> None:
    entity = _entity(EntityType.PAN, "abcde1234f")
    token = apply_action(PolicyAction.TOKENIZE, entity, session_id="s1")

    assert reverse_entity("s1", EntityType.PAN, token) == "ABCDE1234F"


@pytest.mark.parametrize(
    "entity_type", [EntityType.NAME, EntityType.ADDRESS, EntityType.EMAIL, EntityType.DATE]
)
def test_tokenize_raises_for_entity_type_with_no_defined_alphabet(entity_type: EntityType) -> None:
    entity = _entity(entity_type, "some free text")
    with pytest.raises(UnsupportedActionForEntityTypeError):
        apply_action(PolicyAction.TOKENIZE, entity, session_id="s1")


# --- Generalize: DOB -> age band ----------------------------------------------------------


def test_generalize_matches_the_primitive_directly() -> None:
    entity = _entity(EntityType.DATE, "15/06/1990")
    reference_date = date(2026, 7, 28)

    result = apply_action(
        PolicyAction.GENERALIZE, entity, session_id="s1", reference_date=reference_date
    )

    assert result == generalize_dob("15/06/1990", reference_date)


def test_generalize_raises_for_non_date_entity_type() -> None:
    entity = _entity(EntityType.PAN, "ABCDE1234F")
    with pytest.raises(UnsupportedActionForEntityTypeError):
        apply_action(PolicyAction.GENERALIZE, entity, session_id="s1", reference_date=date(2026, 7, 28))


def test_generalize_raises_when_reference_date_missing() -> None:
    entity = _entity(EntityType.DATE, "15/06/1990")
    with pytest.raises(MissingDispatchParameterError):
        apply_action(PolicyAction.GENERALIZE, entity, session_id="s1")


# --- Derive: PIN code -> state / district --------------------------------------------------


def test_derive_state_for_unambiguous_pin() -> None:
    entity = _entity(EntityType.PIN_CODE, "110001")
    result = apply_action(
        PolicyAction.DERIVE, entity, session_id="s1", derive_attribute=DeriveAttribute.STATE
    )
    assert result == "DELHI"


def test_derive_district_for_unambiguous_pin() -> None:
    entity = _entity(EntityType.PIN_CODE, "110001")
    result = apply_action(
        PolicyAction.DERIVE, entity, session_id="s1", derive_attribute=DeriveAttribute.DISTRICT
    )
    assert result == "NEW DELHI"


def test_derive_raises_for_non_pin_code_entity_type() -> None:
    entity = _entity(EntityType.ACCOUNT_NUMBER, "123456789")
    with pytest.raises(UnsupportedActionForEntityTypeError):
        apply_action(PolicyAction.DERIVE, entity, session_id="s1", derive_attribute=DeriveAttribute.STATE)


def test_derive_raises_when_derive_attribute_missing() -> None:
    entity = _entity(EntityType.PIN_CODE, "110001")
    with pytest.raises(MissingDispatchParameterError):
        apply_action(PolicyAction.DERIVE, entity, session_id="s1")


def test_derive_falls_back_to_tokenize_for_genuinely_unknown_pin() -> None:
    entity = _entity(EntityType.PIN_CODE, "999999")
    result = apply_action(
        PolicyAction.DERIVE, entity, session_id="s1", derive_attribute=DeriveAttribute.STATE
    )

    assert result != "999999"
    assert reverse_entity("s1", EntityType.PIN_CODE, result) == "999999"


def test_derive_falls_back_to_tokenize_for_state_ambiguous_pin() -> None:
    entity = _entity(EntityType.PIN_CODE, "110025")
    result = apply_action(
        PolicyAction.DERIVE, entity, session_id="s1", derive_attribute=DeriveAttribute.STATE
    )

    assert result != "110025"
    assert reverse_entity("s1", EntityType.PIN_CODE, result) == "110025"


# --- Determinism ----------------------------------------------------------------------------


def test_tokenize_dispatch_is_deterministic_within_a_session() -> None:
    entity = _entity(EntityType.PAN, "ABCDE1234F")
    first = apply_action(PolicyAction.TOKENIZE, entity, session_id="s-determinism")
    second = apply_action(PolicyAction.TOKENIZE, entity, session_id="s-determinism")
    assert first == second
