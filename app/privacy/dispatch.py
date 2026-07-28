"""Action dispatch: select and invoke the correct privacy primitive (BUILD.md Phase 3,
completing the "Policy Engine" service ARCHITECTURE.md §4.1 describes as "config-driven
transformation. Deterministic by requirement").

Composes the four existing action primitives (detection, tokenize, generalize, derive)
without modifying any of them. No transformation logic lives here -- no encryption, no age
arithmetic, no dataset lookup. This module does exactly two things: (1) a small amount of
input canonicalization ahead of Tokenize, explicitly assigned to "policy-dispatch" by
tokenize.py's own docstring rather than to the primitive itself; (2) routing -- given an
action and a detected entity, calling the one primitive function that implements it.

Which attribute Derive should expose (state vs district) is not decided here --
`derive_attribute` is a required, explicit caller-supplied parameter. Resolving that from
an actual form field is Phase 4 work (BUILD.md Phase 4 task 5, "field actions read from the
form schema and active policy config"); that binding does not exist yet, so this module
does not guess it.
"""

import re
from datetime import date
from enum import Enum

from app.privacy.derive import (
    AmbiguousPinCodeError,
    UnknownPinCodeError,
    derive_district,
    derive_state,
)
from app.privacy.detection import DetectedEntity, EntityType
from app.privacy.generalize import generalize_dob
from app.privacy.tokenize import UnsupportedEntityTypeError, tokenize_entity


class PolicyAction(str, Enum):
    TOKENIZE = "tokenize"
    GENERALIZE = "generalize"
    DERIVE = "derive"
    PASS_THROUGH = "pass_through"


class DeriveAttribute(str, Enum):
    STATE = "state"
    DISTRICT = "district"


class PolicyDispatchError(ValueError):
    """Base class for action-dispatch failures."""


class UnsupportedActionForEntityTypeError(PolicyDispatchError):
    """Raised when the requested action has no defined behavior for the entity's type --
    e.g. Tokenize against NAME/ADDRESS/EMAIL/DATE (no FF1 alphabet defined for free text --
    see tokenize.py), or Generalize/Derive against an entity type neither is pinned to."""


class MissingDispatchParameterError(PolicyDispatchError):
    """Raised when a parameter a specific action requires to dispatch is missing:
    `reference_date` for Generalize, `derive_attribute` for Derive."""


def resolve_action(explicit_action: PolicyAction | None) -> PolicyAction:
    """DECISIONS.md E5/P10, Invariant I4: a detected sensitive value with no explicit
    policy rule is tokenized, never passed through."""
    return explicit_action if explicit_action is not None else PolicyAction.TOKENIZE


def _canonicalize(entity_type: EntityType, text: str) -> str:
    """Reshapes detected text into the alphabet Tokenize expects. Not a privacy
    transformation -- undoing formatting detection.py's own regexes still allow through.
    PIN_CODE and ACCOUNT_NUMBER need no canonicalization: their detection regexes admit no
    embedded separators, so detected text is already canonical."""
    if entity_type == EntityType.PHONE:
        # detection.py's PHONE pattern matches either a bare 10-digit number or a "+91"
        # prefix (with an optional separator) followed by one. Stripping all non-digits and
        # keeping the last 10 handles both shapes uniformly.
        return re.sub(r"\D", "", text)[-10:]
    if entity_type == EntityType.AADHAAR:
        return re.sub(r"[\s-]", "", text)
    if entity_type in (EntityType.PAN, EntityType.PASSPORT):
        return text.upper()
    return text


def apply_action(
    action: PolicyAction,
    entity: DetectedEntity,
    *,
    session_id: str,
    reference_date: date | None = None,
    derive_attribute: DeriveAttribute | None = None,
) -> str:
    if action is PolicyAction.PASS_THROUGH:
        return entity.text

    if action is PolicyAction.TOKENIZE:
        canonical = _canonicalize(entity.entity_type, entity.text)
        try:
            return tokenize_entity(session_id, entity.entity_type, canonical)
        except UnsupportedEntityTypeError as exc:
            raise UnsupportedActionForEntityTypeError(
                f"Tokenize has no defined alphabet for entity type {entity.entity_type.value!r}"
            ) from exc

    if action is PolicyAction.GENERALIZE:
        if entity.entity_type != EntityType.DATE:
            raise UnsupportedActionForEntityTypeError(
                f"Generalize is only defined for {EntityType.DATE.value!r} entities, "
                f"got {entity.entity_type.value!r}"
            )
        if reference_date is None:
            raise MissingDispatchParameterError("Generalize requires reference_date")
        return generalize_dob(entity.text, reference_date)

    if action is PolicyAction.DERIVE:
        if entity.entity_type != EntityType.PIN_CODE:
            raise UnsupportedActionForEntityTypeError(
                f"Derive is only defined for {EntityType.PIN_CODE.value!r} entities, "
                f"got {entity.entity_type.value!r}"
            )
        if derive_attribute is None:
            raise MissingDispatchParameterError("Derive requires derive_attribute")
        try:
            if derive_attribute is DeriveAttribute.STATE:
                return derive_state(entity.text)
            return derive_district(entity.text)
        except (UnknownPinCodeError, AmbiguousPinCodeError):
            # DECISIONS.md P10, extended to the ambiguous case (see derive.py's module
            # docstring): Derive could not produce a confident value -- fail closed via
            # Tokenize rather than passing the raw PIN through.
            canonical = _canonicalize(EntityType.PIN_CODE, entity.text)
            return tokenize_entity(session_id, EntityType.PIN_CODE, canonical)

    raise AssertionError(f"Unhandled PolicyAction: {action!r}")  # exhaustive enum, unreachable
