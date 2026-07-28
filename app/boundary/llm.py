"""Boundary-layer single call site for the generative LLM (BUILD.md Phase 4, task 1).

The only module, besides app.extraction.llm_client itself, permitted to import
generate_structured (Invariant I3) -- enforced by tests/test_import_boundaries.py.

Dispatches on PrivacyMode:
- NONE: calls generate_structured directly, unprotected -- identical to the pre-Phase-4
  call. Still routed through this single call site (I3 is unconditional, not
  mode-dependent); it just does nothing extra. This is the default extract_field uses, so
  every caller that does not explicitly opt into a protected mode keeps today's exact
  behavior.
- FULL_TOKENIZE: protect() with no entity_actions override -- fail-closed Tokenize on
  every detected entity, per app.privacy.dispatch.resolve_action (Invariant I4) -- then
  send, then reverse every string field of the parsed response. Deliberately not made
  "smarter" about entity types Tokenize cannot execute (NAME/ADDRESS/EMAIL/DATE): that gap
  is an existing, already-documented Phase 3 limitation, and BUILD.md's own Phase 4 risk
  table treats the resulting accuracy cost as a finding to measure, not a bug to route
  around.
- POLICY_ENGINE (BUILD.md Phase 4 commit 6): resolves the active named config's decision
  for the one field this call's prompt was built for (app.boundary.policy_engine), then
  protects/sends/reverses exactly as FULL_TOKENIZE does, just with a field-specific
  entity_actions/derive_attribute instead of the blanket default. All of that resolution
  lives in app.boundary.policy_engine -- this function only calls it and passes the
  result to protect(); no privacy_mode-specific policy knowledge lives outside the
  boundary package. field_name/policy_action_ref/reference_date are accepted
  unconditionally (harmless, unused for NONE/FULL_TOKENIZE) so callers never need to know
  which mode is active before deciding what to pass.

`active_policy_config` (BUILD.md Phase 4 commit 8, optional) is dependency-injected rather
than read from app.boundary.policy_engine's module attributes directly: omitted, this call
gets policy_engine.get_default_active_policy_config() (today's only real-caller behavior,
unchanged); supplied, this one call uses that ActivePolicyConfig instead, with no effect on
any other call before or after it. Nothing here is written to or read from mutable shared
state -- each call resolves its own config fresh from its own arguments, which is what makes
"a mode switch, or a differently-configured call, cannot leak into another call" true by
construction rather than by discipline. See tests/boundary/test_state_isolation.py.

`capture` (BUILD.md Phase 4 commit 7, optional, default None) is invoked once per call with
exactly the text that actually left the boundary for `generate_structured` -- the protected
text for FULL_TOKENIZE/POLICY_ENGINE, the raw prompt for NONE (still worth recording: "every
outbound request", not just protected ones). See app.boundary.capture for why this is a
caller-supplied callback rather than a module-global store.
"""

from datetime import date
from typing import TypeVar

from pydantic import BaseModel

from app.boundary import policy_engine
from app.boundary.capture import CapturedPayload, PayloadCapture
from app.boundary.mode import PrivacyMode
from app.boundary.payload import ProtectionContext, protect
from app.extraction.llm_client import generate_structured

T = TypeVar("T", bound=BaseModel)


def generate_structured_protected(
    prompt: str,
    response_schema: type[T],
    *,
    session_id: str,
    privacy_mode: PrivacyMode,
    field_name: str | None = None,
    policy_action_ref: str | None = None,
    reference_date: date | None = None,
    capture: PayloadCapture | None = None,
    active_policy_config: policy_engine.ActivePolicyConfig | None = None,
) -> T:
    if privacy_mode is PrivacyMode.NONE:
        _capture(capture, session_id, privacy_mode, prompt)
        return generate_structured(prompt, response_schema)

    if privacy_mode is PrivacyMode.FULL_TOKENIZE:
        context = protect(prompt, session_id=session_id)
        _capture(capture, session_id, privacy_mode, context.text)
        parsed = generate_structured(context.text, response_schema)
        return _reverse_response_strings(parsed, context)

    if privacy_mode is PrivacyMode.POLICY_ENGINE:
        if field_name is None:
            raise ValueError("privacy_mode=policy_engine requires field_name")
        resolved = active_policy_config or policy_engine.get_default_active_policy_config()
        entity_actions, derive_attribute = policy_engine.resolve_field_policy(
            field_name, policy_action_ref, resolved.config, resolved.derive_attribute
        )
        context = protect(
            prompt,
            session_id=session_id,
            entity_actions=entity_actions,
            reference_date=reference_date,
            derive_attribute=derive_attribute,
        )
        _capture(capture, session_id, privacy_mode, context.text)
        parsed = generate_structured(context.text, response_schema)
        return _reverse_response_strings(parsed, context)

    raise AssertionError(f"Unhandled PrivacyMode: {privacy_mode!r}")  # exhaustive enum, unreachable


def _capture(capture: PayloadCapture | None, session_id: str, privacy_mode: PrivacyMode, prompt: str) -> None:
    if capture is not None:
        capture(CapturedPayload(session_id=session_id, privacy_mode=privacy_mode, prompt=prompt))


def _reverse_response_strings(response: T, context: ProtectionContext) -> T:
    """Reverses every string-valued field of `response`, generically -- the boundary does
    not know or need to know the shape of any particular response schema (today, only
    app.extraction.extractor's _FieldExtractionResponse.value; this must not require this
    module to import or know about that type). context.reverse() is already a no-op for a
    string that contains no recorded token, so fields with nothing to reverse are
    unaffected.
    """
    updates = {name: context.reverse(value) for name, value in response if isinstance(value, str)}
    return response.model_copy(update=updates) if updates else response
