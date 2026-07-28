"""Outbound PII assertion (BUILD.md Phase 4, commit 7): Invariant I2 -- in full_tokenize
and policy_engine modes, no raw PII ground-truth value ever appears in any payload sent to
the external LLM. This is the project's central claim; BUILD.md itself names this "the
loudest test in the suite."

Protected invariant test (CLAUDE.md §4): must never be weakened, skipped, or deleted to
make a change pass. If this test fails, the code is wrong, not the test.

The Phase 6 evaluation dataset (50-60 semi-synthetic cases) does not exist yet -- that is
explicitly later work. This test instead sweeps every entity type the active mechanism can
currently protect, with a real ground-truth value per type, proving the *mechanism* holds
now, ready to be pointed at the real dataset once Phase 6 builds it. Every case here uses
the real app.boundary.llm.generate_structured_protected call site and the real
app.boundary.capture hook (BUILD.md Phase 4 commit 7) -- only the actual network call
(app.boundary.llm.generate_structured) is stubbed, and the stub deliberately echoes
whatever it received back in its response, so both the outbound prompt and the inbound
response are captured and checked.
"""

from datetime import date

import pytest
from pydantic import BaseModel

from app.boundary.capture import CapturedPayload
from app.boundary.llm import generate_structured_protected
from app.boundary.mode import PrivacyMode


class _Response(BaseModel):
    value: str | None


def _echo_stub(prompt: str, response_schema: type) -> _Response:
    return _Response(value=prompt)


# --- FULL_TOKENIZE: every Tokenize-capable entity type ------------------------------------

_FULL_TOKENIZE_CASES = [
    ("PAN", "PAN Number: ABCDE1234F", "ABCDE1234F"),
    ("Aadhaar", "Aadhaar Number: 123456789012", "123456789012"),
    ("Phone", "Phone Number: 9876543210", "9876543210"),
    ("PIN code", "PIN Code: 110001", "110001"),
    ("Account number", "Account Number: 123456789012345", "123456789012345"),
    ("Passport", "Passport Number: A1234567", "A1234567"),
]


@pytest.mark.parametrize(("label", "prompt", "raw_value"), _FULL_TOKENIZE_CASES, ids=[c[0] for c in _FULL_TOKENIZE_CASES])
def test_full_tokenize_mode_never_sends_the_raw_ground_truth_value(
    label: str, prompt: str, raw_value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.boundary.llm.generate_structured", _echo_stub)
    captured: list[CapturedPayload] = []

    generate_structured_protected(
        prompt, _Response, session_id=f"s-{label}", privacy_mode=PrivacyMode.FULL_TOKENIZE, capture=captured.append
    )

    assert len(captured) == 1, f"{label}: outbound payload was not captured at all"
    assert raw_value not in captured[0].prompt, f"{label}: raw ground-truth value leaked into the outbound payload"


# --- POLICY_ENGINE (age_state, the live ACTIVE_POLICY_CONFIG): every governed field --------

_POLICY_ENGINE_CASES = [
    ("pan_number", "pan", "PAN Number: ABCDE1234F", "ABCDE1234F"),
    ("aadhaar_number", "aadhaar", "Aadhaar Number: 123456789012", "123456789012"),
    ("phone_number", "phone", "Phone Number: 9876543210", "9876543210"),
    ("linked_account_number", "account_number", "Account Number: 123456789012345", "123456789012345"),
    ("pin_code", "pincode", "PIN Code: 110001", "110001"),
    ("date_of_birth", "dob", "DOB: 15/06/1990", "15/06/1990"),
]


@pytest.mark.parametrize(
    ("field_name", "policy_action_ref", "prompt", "raw_value"),
    _POLICY_ENGINE_CASES,
    ids=[c[0] for c in _POLICY_ENGINE_CASES],
)
def test_policy_engine_mode_never_sends_the_raw_ground_truth_value(
    field_name: str, policy_action_ref: str, prompt: str, raw_value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.boundary.llm.generate_structured", _echo_stub)
    captured: list[CapturedPayload] = []

    generate_structured_protected(
        prompt,
        _Response,
        session_id=f"s-{field_name}",
        privacy_mode=PrivacyMode.POLICY_ENGINE,
        field_name=field_name,
        policy_action_ref=policy_action_ref,
        reference_date=date(2026, 7, 28),
        capture=captured.append,
    )

    assert len(captured) == 1, f"{field_name}: outbound payload was not captured at all"
    assert raw_value not in captured[0].prompt, (
        f"{field_name}: raw ground-truth value leaked into the outbound payload"
    )


# --- The inbound side: reversal must not reintroduce raw values into anything logged -------


def test_full_tokenize_round_trip_captured_payload_never_contains_the_raw_value_even_after_reversal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The captured payload is a snapshot taken at send time -- reversal happens afterward,
    on the response, inside the boundary, and must never retroactively touch what was
    already captured as having been sent."""
    monkeypatch.setattr("app.boundary.llm.generate_structured", _echo_stub)
    captured: list[CapturedPayload] = []

    result = generate_structured_protected(
        "PAN Number: ABCDE1234F",
        _Response,
        session_id="s1",
        privacy_mode=PrivacyMode.FULL_TOKENIZE,
        capture=captured.append,
    )

    assert result.value == "PAN Number: ABCDE1234F"  # reversed correctly on the way back in
    assert "ABCDE1234F" not in captured[0].prompt  # but what was actually sent stays tokenized
