"""Boundary-layer whole-payload protect/reverse orchestration (BUILD.md Phase 4, task 3).

Composes existing, unmodified app.privacy primitives -- detect_entities, resolve_action,
apply_action, reverse_entity -- into whole-text protect/reverse operations. Owns
orchestration only: which action applies to which detected entity, and how substituted
spans get stitched back into a single string. No detection, transformation, or dataset-
lookup logic lives here; app.privacy stays deterministic primitives only.

Each call to protect() is scoped to one outbound payload (BUILD.md Phase 4 task 3:
"Outbound path: protect -> send. Inbound path: receive -> reverse -> return to trusted
zone"). It resolves an action per detected entity by entity type -- not one action for the
whole text -- because a single LLM call's prompt (evidence for one form field) can still
incidentally contain other entity types that field's own policy action was never meant to
govern; forcing one action across all of them would misapply e.g. Generalize (DATE-only) to
an unrelated PIN code.

protect() returns a ProtectionContext, not a raw token manifest. The manifest -- which
tokens are reversible and what entity type each one is -- is bookkeeping the protection
process needs internally, not something a caller should have to understand, store, or
correctly thread between a protect() call and the later reverse() call. Sealing it (and
session_id) inside the context object also removes a real class of caller error: a
session_id mismatched between protect and reverse would corrupt or fail reversal; with
session_id captured once and reused automatically, that mistake is no longer possible.
"""

from dataclasses import dataclass, field
from datetime import date

from app.privacy.detection import DetectedEntity, EntityType, detect_entities
from app.privacy.dispatch import (
    DeriveAttribute,
    PolicyAction,
    apply_action,
    resolve_action,
)
from app.privacy.tokenize import reverse_entity


@dataclass(frozen=True)
class ProtectionContext:
    """Returned by protect(). `text` is the only field callers are meant to read directly
    -- send it onward. `reverse()` is the only way back; the fields behind it are not part
    of this class's public contract (repr=False also keeps them out of prints/logs)."""

    text: str
    _session_id: str = field(repr=False)
    _token_manifest: dict[str, EntityType] = field(repr=False)

    def reverse(self, response_text: str) -> str:
        return _reverse_tokens(response_text, self._session_id, self._token_manifest)


def protect(
    text: str,
    *,
    session_id: str,
    entity_actions: dict[EntityType, PolicyAction] | None = None,
    reference_date: date | None = None,
    derive_attribute: DeriveAttribute | None = None,
) -> ProtectionContext:
    """Detect every entity in `text`, resolve and apply a PolicyAction per entity (fail-
    closed default via resolve_action -- Invariant I4), and reconstruct the protected
    string. Any PolicyDispatchError from apply_action propagates immediately: protect()
    never returns a partially-protected string, since a caller receiving one could not tell
    which spans are actually safe to send onward (Invariants I2/I4).

    entity_actions overrides resolve_action's default per entity type; an entity type
    absent from it falls through to the existing Tokenize default -- so "protect
    everything with no explicit policy" needs no special-casing here, it is simply
    entity_actions=None.
    """
    entity_actions = entity_actions or {}
    entities = detect_entities(text)

    actions: list[PolicyAction] = []
    results: list[str] = []
    for entity in entities:
        action = resolve_action(entity_actions.get(entity.entity_type))
        result = apply_action(
            action,
            entity,
            session_id=session_id,
            reference_date=reference_date,
            derive_attribute=derive_attribute,
        )
        actions.append(action)
        results.append(result)

    protected_text = _reconstruct(text, entities, results)

    token_manifest = {
        result: entity.entity_type
        for entity, action, result in zip(entities, actions, results)
        if action is PolicyAction.TOKENIZE
    }

    return ProtectionContext(text=protected_text, _session_id=session_id, _token_manifest=token_manifest)


def _reconstruct(text: str, entities: list[DetectedEntity], results: list[str]) -> str:
    """Largest-span-first substitution, skipping any entity fully nested inside an
    already-substituted span -- ADDRESS's documented overlap exception (detection.py): its
    window can contain other already-claimed entities, e.g. a nested PIN_CODE.

    Deliberately not shared with app.privacy.cli's private _reconstruct_protected_text,
    which implements the same algorithm for the CLI's own diagnostic output. That function
    exists for manual inspection, not as a production dependency -- importing it here would
    make the boundary layer depend on CLI internals, inverting the layering CLAUDE.md §6
    requires (a diagnostic tool's private implementation should not become something
    production orchestration code quietly relies on staying stable). The algorithm is
    small and stable; duplicating it here is a deliberate, stated trade-off, not an
    oversight.
    """
    order = sorted(range(len(entities)), key=lambda i: entities[i].end - entities[i].start, reverse=True)
    substituted_spans: list[tuple[int, int]] = []
    used = [False] * len(entities)
    for i in order:
        e = entities[i]
        if any(e.start >= s and e.end <= en for s, en in substituted_spans):
            continue
        substituted_spans.append((e.start, e.end))
        used[i] = True

    pieces: list[str] = []
    cursor = 0
    for i in sorted(range(len(entities)), key=lambda i: entities[i].start):
        if not used[i]:
            continue
        entity, result = entities[i], results[i]
        pieces.append(text[cursor : entity.start])
        pieces.append(result)
        cursor = entity.end
    pieces.append(text[cursor:])
    return "".join(pieces)


def _reverse_tokens(text: str, session_id: str, token_manifest: dict[str, EntityType]) -> str:
    """Finds every occurrence of every known token in `text` once, up front, then splices
    in each token's original value -- largest-span-first, skipping any occurrence that
    overlaps one already accepted, mirroring _reconstruct's own defensiveness. Positions
    are found in the untouched input rather than via sequential str.replace() calls on a
    progressively-mutated string: a longer token's original value can itself contain a
    shorter token as a literal substring (observed directly in testing -- a token ending
    "...9912345" recreated the shorter token "12345" inside its own replacement text), and
    naive sequential replacement would then "reverse" that accidental match too. Only ever
    matches exact strings this session's own protect() call recorded; never re-detects
    entities in `text` and guesses, which could not reliably distinguish a token we
    produced from something else that happens to share its shape.
    """
    matches: list[tuple[int, int, str]] = []
    for token in token_manifest:
        start = text.find(token)
        while start != -1:
            matches.append((start, start + len(token), token))
            start = text.find(token, start + 1)

    matches.sort(key=lambda m: m[1] - m[0], reverse=True)
    accepted: list[tuple[int, int, str]] = []
    for start, end, token in matches:
        if any(start < a_end and end > a_start for a_start, a_end, _ in accepted):
            continue
        accepted.append((start, end, token))
    accepted.sort(key=lambda m: m[0])

    pieces: list[str] = []
    cursor = 0
    for start, end, token in accepted:
        pieces.append(text[cursor:start])
        pieces.append(reverse_entity(session_id, token_manifest[token], token))
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)
