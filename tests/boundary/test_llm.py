"""Tests for app.boundary.llm (BUILD.md Phase 4, commit 4 -- the single call site,
mode-gated; commit 6 -- real POLICY_ENGINE behavior).

NONE mode is proven to be a byte-identical pass-through -- this is what every existing
caller of extract_field relies on for backward compatibility, so it gets the most direct
possible test: the stubbed generate_structured must receive the exact original prompt.

FULL_TOKENIZE is proven twice: once with content Tokenize can handle (round trip works),
and once with content it cannot (NAME) -- asserting the known, already-documented Phase 3
gap raises rather than silently degrading, so this test would fail loudly if that gap were
ever papered over with a silent fallback.

POLICY_ENGINE tests exercise the real, live ACTIVE_POLICY_CONFIG (age_state.json) --
app.boundary.policy_engine's own tests separately cover resolve_field_policy generically
against all three named configs, so these focus on proving generate_structured_protected
wires that resolution into protect()/reverse() correctly for each action shape (Tokenize,
Generalize, Derive) and still falls through to the fail-closed default for a field the
active config does not govern.

No real LLM or FF1 session state leaks between tests: generate_structured is always
stubbed; real protect()/reverse() calls use a fresh session_id per test.
"""

from datetime import date

import pytest
from pydantic import BaseModel

from app.boundary.llm import generate_structured_protected
from app.boundary.mode import PrivacyMode
from app.privacy.dispatch import UnsupportedActionForEntityTypeError


class _Response(BaseModel):
    value: str | None
    note: str | None = None


def test_none_mode_passes_the_prompt_through_unmodified(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_prompts: list[str] = []

    def stub(prompt: str, response_schema: type) -> _Response:
        seen_prompts.append(prompt)
        return _Response(value="unchanged")

    monkeypatch.setattr("app.boundary.llm.generate_structured", stub)

    prompt = "Applicant Full Name: Asha Rao"
    result = generate_structured_protected(
        prompt, _Response, session_id="s1", privacy_mode=PrivacyMode.NONE
    )

    assert seen_prompts == [prompt]
    assert result.value == "unchanged"


def test_full_tokenize_mode_protects_the_prompt_and_reverses_the_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_prompts: list[str] = []

    def stub(prompt: str, response_schema: type) -> _Response:
        seen_prompts.append(prompt)
        # Echo the (now-tokenized) PAN back in the response, as a real extraction would.
        token = prompt.removeprefix("PAN: ")
        return _Response(value=token, note=f"cited {token}")

    monkeypatch.setattr("app.boundary.llm.generate_structured", stub)

    result = generate_structured_protected(
        "PAN: ABCDE1234F", _Response, session_id="s1", privacy_mode=PrivacyMode.FULL_TOKENIZE
    )

    assert seen_prompts[0] != "PAN: ABCDE1234F"  # the real PAN never left the boundary
    assert "ABCDE1234F" not in seen_prompts[0]
    assert result.value == "ABCDE1234F"  # reversed back on the way in
    assert result.note == "cited ABCDE1234F"  # every string field is reversed, not just `value`


def test_full_tokenize_mode_raises_for_an_entity_type_tokenize_cannot_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NAME has no FF1 alphabet (app.privacy.tokenize) -- an existing, already-documented
    Phase 3 gap. This must raise, not silently pass the name through unprotected, which
    would violate Invariants I2/I4."""

    def fail_if_called(prompt: str, response_schema: type) -> _Response:
        raise AssertionError("the LLM must not be called if protect() fails")

    monkeypatch.setattr("app.boundary.llm.generate_structured", fail_if_called)

    with pytest.raises(UnsupportedActionForEntityTypeError):
        generate_structured_protected(
            "Applicant Full Name: Asha Rao",
            _Response,
            session_id="s1",
            privacy_mode=PrivacyMode.FULL_TOKENIZE,
        )


def test_policy_engine_mode_requires_field_name() -> None:
    with pytest.raises(ValueError, match="field_name"):
        generate_structured_protected(
            "irrelevant", _Response, session_id="s1", privacy_mode=PrivacyMode.POLICY_ENGINE
        )


def test_policy_engine_mode_tokenizes_a_governed_identifier_field_and_reverses_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """age_state.json (the live ACTIVE_POLICY_CONFIG) maps pan_number to tokenize."""
    seen_prompts: list[str] = []

    def stub(prompt: str, response_schema: type) -> _Response:
        seen_prompts.append(prompt)
        token = prompt.removeprefix("PAN Number: ")
        return _Response(value=token)

    monkeypatch.setattr("app.boundary.llm.generate_structured", stub)

    result = generate_structured_protected(
        "PAN Number: ABCDE1234F",
        _Response,
        session_id="s1",
        privacy_mode=PrivacyMode.POLICY_ENGINE,
        field_name="pan_number",
        policy_action_ref="pan",
    )

    assert "ABCDE1234F" not in seen_prompts[0]  # the real PAN never left the boundary
    assert result.value == "ABCDE1234F"  # reversed back on the way in


def test_policy_engine_mode_generalizes_a_governed_date_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """age_state.json maps date_of_birth to generalize -- non-reversible, so the age band
    itself is what both the outbound prompt and the returned value should carry."""

    def stub(prompt: str, response_schema: type) -> _Response:
        assert "15/06/1990" not in prompt  # the real DOB never left the boundary
        assert "30-39" in prompt
        return _Response(value="30-39")

    monkeypatch.setattr("app.boundary.llm.generate_structured", stub)

    result = generate_structured_protected(
        "DOB: 15/06/1990",
        _Response,
        session_id="s1",
        privacy_mode=PrivacyMode.POLICY_ENGINE,
        field_name="date_of_birth",
        policy_action_ref="dob",
        reference_date=date(2026, 7, 28),
    )

    assert result.value == "30-39"


def test_policy_engine_mode_derives_a_governed_pin_code_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """age_state.json maps pin_code to derive, exposing state -- non-reversible."""

    def stub(prompt: str, response_schema: type) -> _Response:
        assert "110001" not in prompt  # the real PIN code never left the boundary
        assert "DELHI" in prompt
        return _Response(value="DELHI")

    monkeypatch.setattr("app.boundary.llm.generate_structured", stub)

    result = generate_structured_protected(
        "PIN Code: 110001",
        _Response,
        session_id="s1",
        privacy_mode=PrivacyMode.POLICY_ENGINE,
        field_name="pin_code",
        policy_action_ref="pincode",
    )

    assert result.value == "DELHI"


def test_policy_engine_mode_falls_through_to_fail_closed_tokenize_for_an_ungoverned_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """full_name is not in age_state.json's field_actions at all -- Invariant I4 still
    applies to whatever the active config does not explicitly govern, exactly as it does
    under FULL_TOKENIZE. This must raise, not silently pass the name through."""

    def fail_if_called(prompt: str, response_schema: type) -> _Response:
        raise AssertionError("the LLM must not be called if protect() fails")

    monkeypatch.setattr("app.boundary.llm.generate_structured", fail_if_called)

    with pytest.raises(UnsupportedActionForEntityTypeError):
        generate_structured_protected(
            "Applicant Full Name: Asha Rao",
            _Response,
            session_id="s1",
            privacy_mode=PrivacyMode.POLICY_ENGINE,
            field_name="full_name",
            policy_action_ref="name",
        )
