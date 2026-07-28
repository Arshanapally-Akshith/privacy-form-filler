"""Tests for app.boundary.payload (BUILD.md Phase 4, task 3 -- whole-payload protect/
reverse orchestration).

Most tests exercise the public contract only: protect() -> ProtectionContext -> .reverse().
A few target the private _reconstruct/_reverse_tokens helpers directly, or construct a
ProtectionContext by hand -- justified where the scenario (a specific substring collision
between two tokens, a session_id deliberately mismatched between protect and reverse)
cannot be reliably produced by picking real input text and letting FF1 do its thing.
"""

from datetime import date

import pytest

from app.boundary.payload import (
    ProtectionContext,
    _reconstruct,
    _reverse_tokens,
    protect,
)
from app.privacy.detection import DetectedEntity, EntityType
from app.privacy.dispatch import (
    DeriveAttribute,
    MissingDispatchParameterError,
    PolicyAction,
    UnsupportedActionForEntityTypeError,
)
from app.privacy.tokenize import UnknownSessionError

# --- protect --------------------------------------------------------------------------


def test_protect_tokenizes_by_default() -> None:
    context = protect("PAN: ABCDE1234F", session_id="s1")

    assert "ABCDE1234F" not in context.text
    assert context.reverse(context.text) == "PAN: ABCDE1234F"


def test_protect_respects_entity_actions_override() -> None:
    text = "Applicant Full Name: Asha Rao"
    context = protect(text, session_id="s1", entity_actions={EntityType.NAME: PolicyAction.PASS_THROUGH})

    assert context.text == text


def test_protect_generalizes_date_with_reference_date() -> None:
    context = protect(
        "DOB: 15/06/1990",
        session_id="s1",
        entity_actions={EntityType.DATE: PolicyAction.GENERALIZE},
        reference_date=date(2026, 7, 28),
    )

    assert context.text == "DOB: 30-39"


def test_protect_derives_pin_code() -> None:
    context = protect(
        "PIN: 110001",
        session_id="s1",
        entity_actions={EntityType.PIN_CODE: PolicyAction.DERIVE},
        derive_attribute=DeriveAttribute.STATE,
    )

    assert context.text == "PIN: DELHI"


def test_protect_returns_text_unchanged_when_nothing_is_detected() -> None:
    context = protect("no sensitive content here", session_id="s1")

    assert context.text == "no sensitive content here"


def test_protect_raises_for_unsupported_tokenize_entity_type() -> None:
    """NAME falls through to the fail-closed Tokenize default, which has no FF1 alphabet
    for free text (app.privacy.tokenize) -- a known, already-documented Phase 3 gap, not
    something this commit works around. No partial protected text is ever produced."""
    with pytest.raises(UnsupportedActionForEntityTypeError):
        protect("Applicant Full Name: Asha Rao", session_id="s1")


def test_protect_raises_when_generalize_missing_reference_date() -> None:
    with pytest.raises(MissingDispatchParameterError):
        protect("DOB: 15/06/1990", session_id="s1", entity_actions={EntityType.DATE: PolicyAction.GENERALIZE})


def test_protect_raises_when_derive_missing_derive_attribute() -> None:
    with pytest.raises(MissingDispatchParameterError):
        protect("PIN: 110001", session_id="s1", entity_actions={EntityType.PIN_CODE: PolicyAction.DERIVE})


# --- _reconstruct ------------------------------------------------------------------------


def test_reconstruct_skips_entities_nested_in_a_larger_substituted_span() -> None:
    """Mirrors app.privacy.cli's own test for the same algorithm, independently
    implemented here (see _reconstruct's docstring for why it is not shared)."""
    text = "123 MG Road 110001 end"
    address = DetectedEntity(EntityType.ADDRESS, text[0:18], 0, 18)
    pin = DetectedEntity(EntityType.PIN_CODE, "110001", 12, 18)
    entities = [pin, address]
    results = ["<PIN_CODE:TOKEN>", "<ADDRESS>"]

    protected = _reconstruct(text, entities, results)

    assert protected == "<ADDRESS> end"


def test_reconstruct_returns_text_unchanged_with_no_entities() -> None:
    assert _reconstruct("plain text", [], []) == "plain text"


# --- _reverse_tokens ----------------------------------------------------------------------


def test_reverse_tokens_prefers_longest_token_when_one_is_a_substring_of_another(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def stub_reverse_entity(session_id: str, entity_type: EntityType, token: str) -> str:
        return f"ORIGINAL[{token}]"

    monkeypatch.setattr("app.boundary.payload.reverse_entity", stub_reverse_entity)

    token_manifest = {"12345": EntityType.PIN_CODE, "9912345": EntityType.PHONE}
    result = _reverse_tokens("value=9912345 end", "s1", token_manifest)

    assert result == "value=ORIGINAL[9912345] end"


def test_reverse_tokens_ignores_tokens_not_present_in_the_text(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def stub_reverse_entity(session_id: str, entity_type: EntityType, token: str) -> str:
        calls.append(token)
        return "ORIGINAL"

    monkeypatch.setattr("app.boundary.payload.reverse_entity", stub_reverse_entity)

    result = _reverse_tokens("no tokens here", "s1", {"ABSENT123": EntityType.PAN})

    assert result == "no tokens here"
    assert calls == []


# --- ProtectionContext.reverse -------------------------------------------------------------


def test_context_reverse_round_trips_a_tokenized_value() -> None:
    context = protect("PAN: ABCDE1234F", session_id="s1")
    token = context.text.removeprefix("PAN: ")
    simulated_llm_response = f'{{"value": "{token}"}}'

    assert context.reverse(simulated_llm_response) == '{"value": "ABCDE1234F"}'


def test_context_reverse_raises_for_a_token_from_a_different_session() -> None:
    real_context = protect("PAN: ABCDE1234F", session_id="s1")
    token = real_context.text.removeprefix("PAN: ")
    mismatched_context = ProtectionContext(
        text="unused",
        _session_id="a-session-that-never-tokenized-anything",
        _token_manifest={token: EntityType.PAN},
    )

    with pytest.raises(UnknownSessionError):
        mismatched_context.reverse(token)


def test_context_reverse_is_identity_when_manifest_is_empty() -> None:
    context = protect("nothing sensitive", session_id="s1")

    assert context.reverse("some llm response") == "some llm response"
