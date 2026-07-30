"""Adversarial mechanism tests: can a Tokenize output be reversed without the real,
matching session state? (Phase 7 Commit 3; ARCHITECTURE.md §8 "reversal attacks", V14.)

**What this file is, and is not.** ARCH §8 draws an explicit line between two different
failure classes: "the adversarial suite tests whether the *mechanism* can be bypassed... the
re-identification analysis tests whether the *policy design* leaks even when the mechanism
works perfectly." This file is squarely the first kind -- it plays the role of an attacker
who has intercepted a tokenized value (e.g. from a captured outbound payload) and tries to
recover the original plaintext *without* the one thing that is supposed to make that
impossible: the real, in-memory session key (DECISIONS.md R8 -- session-scoped, process-
memory only, never durable, never transmitted).

These are mechanism tests, not an evaluation script: no dataset, no LLM, no case fixtures --
just the real app.privacy.tokenize public API (tokenize_value/tokenize_entity,
reverse_token/reverse_entity, SessionPseudonymMap), exercised directly. Nothing here
reimplements FF1 or key management; every attack is expressed as a call into the same
functions app.boundary.payload.protect()/ProtectionContext.reverse() already use in
production.

**Explicitly out of scope: cryptanalyzing FF1 itself.** Whether AES-based FF1 is
mathematically invertible without its key is a primitive-level guarantee, already exercised
against NIST SP 800-38G test vectors in tests/privacy/test_ff1.py. Attempting to brute-force
or cryptanalyze it here would both duplicate that coverage and overstate what a unit test
can meaningfully prove about a real cipher's strength. What this file tests instead is the
mechanism's *key-management surface*: does anything in tokenize.py ever hand out a correct
plaintext to a caller who does not possess the exact matching session key -- a question a
deterministic, offline test can actually answer.

**Result summary (all three scenarios below are expected failed attacks):**
  - No attempt in this file recovers a victim's real plaintext without the matching session
    key. Every attack either raises UnknownSessionError (loudly, per CLAUDE.md §5) or
    silently produces a value that is provably *not* the original.
  - There is no "expected successful attack" bucket in this file -- unlike the detection-
    evasion suite (test_adversarial_detection_evasion.py), which does have confirmed,
    accepted-limitation bypasses to report, reversal-without-the-real-key has none here.
  - If any test below ever started passing a wrong value through as if it were correct, or
    ever recovered a real plaintext without the matching session key, that would be an
    Invariant I2 violation (no raw PII outbound in protected modes) serious enough to
    escalate immediately -- not a limitation to document and move past, and not something
    this commit's own instructions permit fixing quietly.
"""

import pytest

from app.privacy.detection import EntityType
from app.privacy.tokenize import (
    SessionPseudonymMap,
    UnknownSessionError,
    reverse_entity,
    reverse_token,
    tokenize_entity,
    tokenize_value,
)

_DIGITS = "0123456789"


def _unique_session(name: str) -> str:
    return f"adversarial-reversal-{name}"


# ---------------------------------------------------------------------------
# Scenario 1: attacker has zero session state at all.
#
# Models an attacker who intercepted a tokenized value (e.g. off the wire, or from a
# captured payload) but has no access whatsoever to the trusted boundary's real, in-memory
# key store -- only their own, entirely separate SessionPseudonymMap instance, which has
# never seen this session_id. This is the literal case the requirement names: "surrogate
# reversal without session state." Expected failed attack: raises loudly, per
# UnknownSessionError's own documented purpose ("a missing key must fail loudly, not
# silently produce a wrong-but-plausible decrypted string").
# ---------------------------------------------------------------------------


def test_reversal_with_zero_session_state_raises_even_when_session_id_is_known() -> None:
    victim_session = _unique_session("victim-zero-state")
    real_value = "987654321098"
    # The real, process-global map (imported implicitly by tokenize_value) creates and
    # holds the actual key -- exactly what app.boundary.payload.protect() does in
    # production.
    token = tokenize_value(victim_session, _DIGITS, real_value)

    # The attacker's own map: freshly constructed, has never seen victim_session at all --
    # simulates an attacker with no access to the trusted boundary's process memory (R8).
    attacker_map = SessionPseudonymMap()
    with pytest.raises(UnknownSessionError):
        attacker_map.get_key(victim_session)

    # Confirms the token itself is still exactly what a real caller received -- the attack
    # fails on key lookup, not because the token was somehow already invalid.
    assert token != real_value
    assert len(token) == len(real_value)


# ---------------------------------------------------------------------------
# Scenario 2: attacker guesses a plausible-looking session_id that was never created.
#
# Session IDs in this system are case IDs (R16) -- not secret, and plausibly guessable in
# shape (e.g. "case-<n>"). This confirms that knowing (or guessing) the *shape* of a real
# session_id is not sufficient on its own: only a session_id this process actually created a
# key for can ever be reversed against. Expected failed attack.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "guessed_session_id",
    [
        "case-1",
        "case-00000001",
        "kyc_account_opening__none",  # shaped like this project's own real run_case_id (eval/harness/run_matrix.py)
        "",
    ],
)
def test_reversal_under_a_plausible_but_never_created_session_id_raises(guessed_session_id: str) -> None:
    token = tokenize_value(_unique_session("victim-guess-target"), _DIGITS, "111122223333")
    with pytest.raises(UnknownSessionError):
        reverse_token(guessed_session_id, _DIGITS, token)


# ---------------------------------------------------------------------------
# Scenario 3: attacker holds a real, legitimate session key -- just the wrong one.
#
# The strongest version of this attack: not an outsider with no state at all, but another
# real, live session (e.g. a different case being processed concurrently) whose own key the
# attacker *does* legitimately possess. This tests session isolation's actual security
# property under attack framing, not just the "different token" check test_tokenize.py
# already makes for two honestly-generated values: here, the attacker actively tries to use
# their own real key to decrypt someone else's token. Expected failed attack: reverse_token
# never raises for this case (the session genuinely exists), but the returned value is
# provably never the victim's real plaintext.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entity_type", "real_value"),
    [
        (EntityType.AADHAAR, "123456789012"),
        (EntityType.PHONE, "9876543210"),
        (EntityType.PIN_CODE, "110001"),
        (EntityType.ACCOUNT_NUMBER, "123456789012345"),
        (EntityType.PAN, "ABCDE1234F"),
        (EntityType.PASSPORT, "A1234567"),
    ],
)
def test_cross_session_reversal_never_recovers_the_real_value(
    entity_type: EntityType, real_value: str
) -> None:
    victim_session = _unique_session(f"victim-cross-{entity_type.value}")
    attacker_session = _unique_session(f"attacker-cross-{entity_type.value}")

    token = tokenize_entity(victim_session, entity_type, real_value)
    # The attacker's session is real -- they legitimately created it themselves, exactly as
    # any other case would (e.g. their own, unrelated form submission) -- so this call
    # succeeds and does not raise; it must simply never produce the victim's real value.
    tokenize_entity(attacker_session, entity_type, real_value)

    recovered_with_wrong_key = reverse_entity(attacker_session, entity_type, token)

    assert recovered_with_wrong_key != real_value
    # Format-preservation still holds even under attack -- FF1 decryption with any valid
    # key on a well-formed token always produces *something* shaped like the alphabet, so
    # a caller cannot distinguish "wrong key" from "right key" by output shape alone. That
    # is expected FF1 behavior, not a partial success for the attacker: shape alone does
    # not recover the value.
    assert len(recovered_with_wrong_key) == len(real_value)


def test_cross_session_reversal_is_deterministic_but_still_wrong_on_repeated_attempts() -> None:
    """A patient attacker retrying the same wrong key repeatedly gains nothing new -- FF1 is
    deterministic given a key, so the wrong output is the same wrong output every time, not
    a probabilistic guess that might eventually land on the truth."""
    victim_session = _unique_session("victim-repeat")
    attacker_session = _unique_session("attacker-repeat")
    real_value = "555566667777"

    token = tokenize_value(victim_session, _DIGITS, real_value)
    tokenize_value(attacker_session, _DIGITS, "000000000000")

    first_attempt = reverse_token(attacker_session, _DIGITS, token)
    second_attempt = reverse_token(attacker_session, _DIGITS, token)

    assert first_attempt == second_attempt
    assert first_attempt != real_value
