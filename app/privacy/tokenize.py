"""Tokenize action with session-scoped pseudonym map (BUILD.md Phase 3, tasks 2, 5, 6).

Session-scoped key, not a value->token dictionary. FF1 is a real, invertible cipher: given
the same key, the same input always encrypts to the same output (same value -> same token
within a session, for free), and decryption recovers the original exactly (reversal, for
free) -- with no growing lookup table required at all. So the session map's job is smaller
than it sounds: generate one cryptographically random key per session
(secrets.token_bytes), keep it only in memory for that session's lifetime, and let FF1's
own mathematical properties do the rest. Two different sessions get two different random
keys, so the same value tokenizes differently across sessions (session isolation), also for
free.

No tweak: this project has no per-call domain-separation requirement beyond the session key
itself, so an empty tweak is used throughout -- simpler than threading a tweak value through
every call site for no present benefit (CLAUDE.md §9, no speculative configurability).

Mirrors app.retrieval.store's CaseIndexRegistry / case_index_registry pattern: a class plus
a process-global instance, in-memory, not durable across restarts (DECISIONS.md R8/R14).
Depends on app.privacy.detection.EntityType (an internal app.privacy import, not a boundary
violation) and app.privacy.ff1 (the generic, alphabet-agnostic primitive) -- ff1.py has no
notion of "session," "entity type," or a minimum domain size policy; those live here.
"""

import secrets

from app.privacy.constants import (
    ALPHABET_ALPHANUMERIC,
    ALPHABET_DIGITS,
    MINIMUM_FF1_DOMAIN_SIZE,
)
from app.privacy.detection import EntityType
from app.privacy.ff1 import decrypt as _ff1_decrypt
from app.privacy.ff1 import encrypt as _ff1_encrypt

_TWEAK = b""
_SESSION_KEY_BYTES = 32  # AES-256


class UnknownSessionError(ValueError):
    """Raised when reversing a token under a session_id with no key on record. A missing
    key must fail loudly, not silently produce a wrong-but-plausible decrypted string."""


class DomainTooSmallError(ValueError):
    """Raised when alphabet/length falls below NIST SP 800-38G's minimum domain size for
    FF1 (MINIMUM_FF1_DOMAIN_SIZE) -- tokenizing it anyway would be weak, not just unusual."""


class UnsupportedEntityTypeError(ValueError):
    """Raised for an EntityType with no defined tokenization alphabet (e.g. NAME, ADDRESS --
    free text has no fixed alphabet/domain to preserve)."""


class SessionPseudonymMap:
    def __init__(self) -> None:
        self._keys: dict[str, bytes] = {}

    def get_or_create_key(self, session_id: str) -> bytes:
        if session_id not in self._keys:
            self._keys[session_id] = secrets.token_bytes(_SESSION_KEY_BYTES)
        return self._keys[session_id]

    def get_key(self, session_id: str) -> bytes:
        if session_id not in self._keys:
            raise UnknownSessionError(f"Unknown session_id: {session_id!r}")
        return self._keys[session_id]


# Process-global, in-memory (DECISIONS.md R14), mirroring app.retrieval.store.case_index_registry.
session_pseudonym_map = SessionPseudonymMap()


def _validate_value(alphabet: str, value: str) -> None:
    if not value:
        raise ValueError("Cannot tokenize an empty value")
    invalid_chars = sorted({ch for ch in value if ch not in alphabet})
    if invalid_chars:
        raise ValueError(f"Value contains characters outside the given alphabet: {invalid_chars}")
    domain_size = len(alphabet) ** len(value)
    if domain_size < MINIMUM_FF1_DOMAIN_SIZE:
        raise DomainTooSmallError(
            f"Domain size {domain_size} (alphabet size {len(alphabet)}, length {len(value)}) "
            f"is below the NIST SP 800-38G minimum of {MINIMUM_FF1_DOMAIN_SIZE}"
        )


def tokenize_value(session_id: str, alphabet: str, value: str) -> str:
    _validate_value(alphabet, value)
    key = session_pseudonym_map.get_or_create_key(session_id)
    return _ff1_encrypt(key, _TWEAK, alphabet, value)


def reverse_token(session_id: str, alphabet: str, token: str) -> str:
    _validate_value(alphabet, token)
    key = session_pseudonym_map.get_key(session_id)
    return _ff1_decrypt(key, _TWEAK, alphabet, token)


# Identifier-shaped, fixed-format entity types only. NAME/ADDRESS/EMAIL/DATE are
# deliberately absent -- free text has no fixed alphabet to preserve, and deciding their
# fallback treatment is a policy-dispatch question for a later commit, not this one.
_ENTITY_ALPHABETS: dict[EntityType, str] = {
    EntityType.AADHAAR: ALPHABET_DIGITS,
    EntityType.PHONE: ALPHABET_DIGITS,
    EntityType.PIN_CODE: ALPHABET_DIGITS,
    EntityType.ACCOUNT_NUMBER: ALPHABET_DIGITS,
    EntityType.PAN: ALPHABET_ALPHANUMERIC,
    EntityType.PASSPORT: ALPHABET_ALPHANUMERIC,
}


def _entity_alphabet(entity_type: EntityType) -> str:
    alphabet = _ENTITY_ALPHABETS.get(entity_type)
    if alphabet is None:
        raise UnsupportedEntityTypeError(f"No tokenization alphabet defined for {entity_type.value!r}")
    return alphabet


def tokenize_entity(session_id: str, entity_type: EntityType, value: str) -> str:
    """`value` must already be in the entity type's canonical alphabet (e.g. uppercase
    alphanumeric for PAN, digits only for phone -- no '+91' prefix, no spaces). Normalizing
    real-world detected text into canonical form is a policy-dispatch concern, not this
    primitive's job."""
    return tokenize_value(session_id, _entity_alphabet(entity_type), value)


def reverse_entity(session_id: str, entity_type: EntityType, token: str) -> str:
    return reverse_token(session_id, _entity_alphabet(entity_type), token)
