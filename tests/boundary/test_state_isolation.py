"""Behavioral security invariants around dependency injection and mode switching
(BUILD.md Phase 4, commit 8).

BUILD.md's Phase 4 testing requirements name two properties this file closes out:
"three-mode parity" (all modes complete without error on the same content) and "mode
switching does not leak state between requests." A third, closely related property is
tested here too: app.boundary.llm.generate_structured_protected's active_policy_config
injection seam (commit 8) must actually take effect per call, and must not leak into a
call that does not use it -- the whole reason it exists as an injected parameter rather
than a second module-global is to make that true by construction.

These tests deliberately assert on *behavior* (what got sent, what came back, whether an
exception was raised) rather than on internals like which module attribute was read --
implementation details are free to change; these properties must not.
"""

from pathlib import Path

import pytest
from pydantic import BaseModel

from app.boundary.llm import generate_structured_protected
from app.boundary.mode import PrivacyMode
from app.boundary.policy_engine import (
    ActivePolicyConfig,
    get_default_active_policy_config,
)
from app.privacy.dispatch import DeriveAttribute, UnsupportedActionForEntityTypeError
from app.privacy.policy_config import load_policy_config

CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent / "app" / "config" / "policy_configs"


class _Response(BaseModel):
    value: str | None


def _echo_stub(prompt: str, response_schema: type) -> _Response:
    return _Response(value=prompt)


# --- active_policy_config injection: default behavior is preserved -------------------------


def test_omitting_active_policy_config_uses_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.boundary.llm.generate_structured", _echo_stub)

    result = generate_structured_protected(
        "PIN Code: 110001",
        _Response,
        session_id="s1",
        privacy_mode=PrivacyMode.POLICY_ENGINE,
        field_name="pin_code",
        policy_action_ref="pincode",
    )

    # Default active config is age_state -- pin_code is Derive, exposing state.
    assert result.value == "PIN Code: DELHI"


def test_injecting_the_default_explicitly_is_equivalent_to_omitting_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.boundary.llm.generate_structured", _echo_stub)

    result = generate_structured_protected(
        "PIN Code: 110001",
        _Response,
        session_id="s1",
        privacy_mode=PrivacyMode.POLICY_ENGINE,
        field_name="pin_code",
        policy_action_ref="pincode",
        active_policy_config=get_default_active_policy_config(),
    )

    assert result.value == "PIN Code: DELHI"


# --- injection actually changes behavior -----------------------------------------------------


def test_injecting_a_different_config_changes_the_resolved_action(monkeypatch: pytest.MonkeyPatch) -> None:
    """strict.json tokenizes pin_code instead of deriving it -- injecting it must produce a
    genuinely different outcome than the default (age_state, which derives), proving this
    is a real behavioral seam and not a parameter that is silently ignored."""
    seen_prompts: list[str] = []

    def stub(prompt: str, response_schema: type) -> _Response:
        seen_prompts.append(prompt)
        return _Response(value=prompt)

    monkeypatch.setattr("app.boundary.llm.generate_structured", stub)
    strict = ActivePolicyConfig(config=load_policy_config(CONFIGS_DIR / "strict.json"), derive_attribute=None)

    generate_structured_protected(
        "PIN Code: 110001",
        _Response,
        session_id="s1",
        privacy_mode=PrivacyMode.POLICY_ENGINE,
        field_name="pin_code",
        policy_action_ref="pincode",
        active_policy_config=strict,
    )

    assert "DELHI" not in seen_prompts[0]  # not derived under strict
    assert "110001" not in seen_prompts[0]  # tokenized instead -- the raw PIN still never leaves


# --- sequential calls with different injected configs do not leak into each other -----------


def test_sequential_calls_with_different_injected_configs_do_not_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same field, same prompt, two back-to-back calls under two different injected
    configs -- each call's own outcome must reflect only its own injected config."""
    seen_prompts: list[str] = []

    def stub(prompt: str, response_schema: type) -> _Response:
        seen_prompts.append(prompt)
        return _Response(value=prompt)

    monkeypatch.setattr("app.boundary.llm.generate_structured", stub)
    strict = ActivePolicyConfig(config=load_policy_config(CONFIGS_DIR / "strict.json"), derive_attribute=None)
    age_state = ActivePolicyConfig(
        config=load_policy_config(CONFIGS_DIR / "age_state.json"), derive_attribute=DeriveAttribute.STATE
    )

    generate_structured_protected(
        "PIN Code: 110001",
        _Response,
        session_id="s1",
        privacy_mode=PrivacyMode.POLICY_ENGINE,
        field_name="pin_code",
        policy_action_ref="pincode",
        active_policy_config=strict,
    )
    generate_structured_protected(
        "PIN Code: 110001",
        _Response,
        session_id="s2",
        privacy_mode=PrivacyMode.POLICY_ENGINE,
        field_name="pin_code",
        policy_action_ref="pincode",
        active_policy_config=age_state,
    )

    assert "DELHI" not in seen_prompts[0]  # first call: strict -- tokenized, not derived
    assert "DELHI" in seen_prompts[1]  # second call: age_state -- derived, unaffected by the first call


def test_a_call_that_injects_nothing_after_one_that_did_still_gets_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An injected config must not overwrite the module-level default for callers that
    never inject anything -- proves injection is per-call, not a hidden global mutation."""
    seen_prompts: list[str] = []

    def stub(prompt: str, response_schema: type) -> _Response:
        seen_prompts.append(prompt)
        return _Response(value=prompt)

    monkeypatch.setattr("app.boundary.llm.generate_structured", stub)
    strict = ActivePolicyConfig(config=load_policy_config(CONFIGS_DIR / "strict.json"), derive_attribute=None)

    generate_structured_protected(
        "PIN Code: 110001",
        _Response,
        session_id="s1",
        privacy_mode=PrivacyMode.POLICY_ENGINE,
        field_name="pin_code",
        policy_action_ref="pincode",
        active_policy_config=strict,
    )
    generate_structured_protected(
        "PIN Code: 110001",
        _Response,
        session_id="s2",
        privacy_mode=PrivacyMode.POLICY_ENGINE,
        field_name="pin_code",
        policy_action_ref="pincode",
        # no active_policy_config -- must still get the real default (age_state), not
        # whatever the previous call injected.
    )

    assert "DELHI" not in seen_prompts[0]
    assert "DELHI" in seen_prompts[1]


# --- fail-closed default holds regardless of which config is injected -----------------------


def test_fail_closed_default_holds_under_an_injected_config_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invariant I4 is enforced by resolve_action/protect(), not by any particular config --
    an ungoverned field must still fail closed no matter which ActivePolicyConfig is
    injected."""

    def fail_if_called(prompt: str, response_schema: type) -> _Response:
        raise AssertionError("the LLM must not be called if protect() fails")

    monkeypatch.setattr("app.boundary.llm.generate_structured", fail_if_called)
    strict = ActivePolicyConfig(config=load_policy_config(CONFIGS_DIR / "strict.json"), derive_attribute=None)

    with pytest.raises(UnsupportedActionForEntityTypeError):
        generate_structured_protected(
            "Applicant Full Name: Asha Rao",
            _Response,
            session_id="s1",
            privacy_mode=PrivacyMode.POLICY_ENGINE,
            field_name="full_name",
            policy_action_ref="name",
            active_policy_config=strict,
        )


# --- mode switching across sequential calls does not leak state -----------------------------


def test_switching_from_full_tokenize_to_none_does_not_leak_protection_into_the_next_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FULL_TOKENIZE call followed immediately by a NONE-mode call for the same content
    must not carry any protection over -- NONE means unprotected, unconditionally."""
    seen_prompts: list[str] = []

    def stub(prompt: str, response_schema: type) -> _Response:
        seen_prompts.append(prompt)
        return _Response(value=prompt)

    monkeypatch.setattr("app.boundary.llm.generate_structured", stub)

    generate_structured_protected(
        "PAN Number: ABCDE1234F", _Response, session_id="s1", privacy_mode=PrivacyMode.FULL_TOKENIZE
    )
    generate_structured_protected(
        "PAN Number: ABCDE1234F", _Response, session_id="s2", privacy_mode=PrivacyMode.NONE
    )

    assert "ABCDE1234F" not in seen_prompts[0]  # protected
    assert seen_prompts[1] == "PAN Number: ABCDE1234F"  # unprotected -- byte-identical passthrough


def test_switching_from_none_to_full_tokenize_still_protects_the_next_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reverse order: an unprotected call first must not disable protection for a
    protected call that follows it."""
    seen_prompts: list[str] = []

    def stub(prompt: str, response_schema: type) -> _Response:
        seen_prompts.append(prompt)
        return _Response(value=prompt)

    monkeypatch.setattr("app.boundary.llm.generate_structured", stub)

    generate_structured_protected(
        "PAN Number: ABCDE1234F", _Response, session_id="s1", privacy_mode=PrivacyMode.NONE
    )
    generate_structured_protected(
        "PAN Number: ABCDE1234F", _Response, session_id="s2", privacy_mode=PrivacyMode.FULL_TOKENIZE
    )

    assert seen_prompts[0] == "PAN Number: ABCDE1234F"
    assert "ABCDE1234F" not in seen_prompts[1]


# --- three-mode parity: all three modes complete without error for the same content ---------


@pytest.mark.parametrize("privacy_mode", [PrivacyMode.NONE, PrivacyMode.FULL_TOKENIZE, PrivacyMode.POLICY_ENGINE])
def test_all_three_modes_complete_without_error_for_the_same_governed_field(
    privacy_mode: PrivacyMode, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.boundary.llm.generate_structured", _echo_stub)

    result = generate_structured_protected(
        "PAN Number: ABCDE1234F",
        _Response,
        session_id=f"s-{privacy_mode.value}",
        privacy_mode=privacy_mode,
        field_name="pan_number",
        policy_action_ref="pan",
    )

    assert result.value is not None
