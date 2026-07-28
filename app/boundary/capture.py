"""Outbound payload capture (BUILD.md Phase 4, commit 7): "record every outbound request
when enabled" -- for assertion (Invariant I2: no raw PII in a protected-mode payload) and
debugging.

Deliberately request-scoped, not a module-global store. The tempting default for "capture
every outbound payload" is a process-global list something appends to on every call --
exactly the kind of shared mutable state that makes concurrent requests and test isolation
fragile, and precisely what CLAUDE.md's general dependency-direction discipline (§6, "no
duplicated global state") argues against. Instead, a caller that wants payloads captured
supplies its own callback per call (app.boundary.llm.generate_structured_protected's
`capture` parameter); nothing here accumulates anything on its own. A caller that wants a
list just passes `captured.append`.
"""

from collections.abc import Callable
from dataclasses import dataclass

from app.boundary.mode import PrivacyMode


@dataclass(frozen=True)
class CapturedPayload:
    session_id: str
    privacy_mode: PrivacyMode
    prompt: str


PayloadCapture = Callable[[CapturedPayload], None]
