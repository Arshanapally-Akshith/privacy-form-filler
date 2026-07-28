"""POLICY_ENGINE mode support (BUILD.md Phase 4, commit 6): resolves a field's per-entity
policy from the active named config, giving app.boundary.llm exactly the
(entity_actions, derive_attribute) protect() needs for one field's prompt.

Which named config is "active" has a fixed default here (ACTIVE_POLICY_CONFIG_NAME), not a
caller-selectable *request* parameter -- letting an HTTP caller choose a specific config
would mean extending the request contract Commit 5 already froze, which stays out of scope.
`age_state` is chosen deliberately over `strict`: it is the only way to prove Generalize and
Derive resolution (not just Tokenize) actually works end to end through a real request, and
both underlying primitives (generalize_dob, derive_state) are already Phase 3-tested;
`strict` alone would make policy_engine mode observably identical to full_tokenize mode for
every field. `resolve_field_policy` below is nonetheless a pure function of an explicit
`config` argument, not hardcoded to the active one -- every named config is exercised
directly by tests/boundary/test_policy_engine.py, proving the mechanism generalizes rather
than just working for the one config wired in live.

CLAUDE.md §5 ("invalid configuration fails at load time, not at first use"): the default
active config is loaded and co-occurrence-guarded once, here, at import time -- not lazily
on the first real policy_engine request.

ActivePolicyConfig / get_default_active_policy_config() (BUILD.md Phase 4, commit 8) exist
so app.boundary.llm.generate_structured_protected can accept the active config as an
injected dependency instead of reaching for the ACTIVE_POLICY_CONFIG module attribute
directly. The module-level default below is still the only config any real caller uses
today (no injection call site exists outside tests yet) -- this is preparing the seam a
future commit would need to let a config be chosen per request, not building that request
plumbing now. Bundled into one object rather than two loose parameters so a caller cannot
inject a config without also supplying the derive_attribute that config actually needs.
"""

from dataclasses import dataclass
from pathlib import Path

from app.privacy.detection import EntityType
from app.privacy.dispatch import DeriveAttribute, PolicyAction
from app.privacy.policy_config import (
    PolicyConfig,
    check_cooccurrence_guard,
    load_policy_config,
)

_POLICY_CONFIGS_DIR = Path(__file__).resolve().parent.parent / "config" / "policy_configs"

ACTIVE_POLICY_CONFIG_NAME = "age_state"

# app.privacy.policy_config.PolicyConfig has no field for which Derive attribute a config
# exposes (ARCH §5.3's own schema doesn't carry it) -- mirrors app.privacy.cli's own
# _CONFIG_DERIVE_ATTRIBUTES, the only other place in the codebase resolving this today.
_CONFIG_DERIVE_ATTRIBUTES: dict[str, DeriveAttribute] = {
    "age_state": DeriveAttribute.STATE,
    "ageband_city": DeriveAttribute.DISTRICT,
}

# app.config.form_schema.FormFieldSpec.policy_action_ref is a form-schema-owned,
# entity-type-shaped string, deliberately not the same vocabulary as EntityType (its own
# docstring: "what this key means... is owned by the policy engine"). Every value in both
# committed schemas already matches EntityType.value exactly except these two, named
# differently when the schemas were authored in Phase 2.
_POLICY_ACTION_REF_TO_ENTITY_TYPE_OVERRIDES: dict[str, EntityType] = {
    "dob": EntityType.DATE,
    "pincode": EntityType.PIN_CODE,
}


def _resolve_entity_type(policy_action_ref: str | None) -> EntityType | None:
    if policy_action_ref is None:
        return None
    if policy_action_ref in _POLICY_ACTION_REF_TO_ENTITY_TYPE_OVERRIDES:
        return _POLICY_ACTION_REF_TO_ENTITY_TYPE_OVERRIDES[policy_action_ref]
    try:
        return EntityType(policy_action_ref)
    except ValueError:
        return None


def load_and_guard_config(config_name: str) -> PolicyConfig:
    """Loads a named config and runs the co-occurrence guard against it (BUILD.md Phase 4
    commit 2), resolving each Derive field's exposed attribute from
    _CONFIG_DERIVE_ATTRIBUTES generically -- reusable for any named config, not just the
    active one, so tests can prove the guard integration holds for all three without
    duplicating this wiring."""
    config = load_policy_config(_POLICY_CONFIGS_DIR / f"{config_name}.json")
    derive_attribute = _CONFIG_DERIVE_ATTRIBUTES.get(config_name)
    derive_attributes = (
        {name: derive_attribute for name, action in config.field_actions.items() if action is PolicyAction.DERIVE}
        if derive_attribute is not None
        else {}
    )
    check_cooccurrence_guard(config, derive_attributes=derive_attributes)
    return config


ACTIVE_POLICY_CONFIG: PolicyConfig = load_and_guard_config(ACTIVE_POLICY_CONFIG_NAME)
ACTIVE_POLICY_CONFIG_DERIVE_ATTRIBUTE: DeriveAttribute | None = _CONFIG_DERIVE_ATTRIBUTES.get(ACTIVE_POLICY_CONFIG_NAME)


@dataclass(frozen=True)
class ActivePolicyConfig:
    """A PolicyConfig bundled with the one Derive attribute it exposes -- the exact pair
    resolve_field_policy needs, injectable as a single unit so the two can never be
    supplied mismatched."""

    config: PolicyConfig
    derive_attribute: DeriveAttribute | None


def get_default_active_policy_config() -> ActivePolicyConfig:
    """The module-level default (age_state) -- what every call gets unless a caller injects
    a different ActivePolicyConfig explicitly."""
    return ActivePolicyConfig(config=ACTIVE_POLICY_CONFIG, derive_attribute=ACTIVE_POLICY_CONFIG_DERIVE_ATTRIBUTE)


def resolve_field_policy(
    field_name: str,
    policy_action_ref: str | None,
    config: PolicyConfig,
    config_derive_attribute: DeriveAttribute | None,
) -> tuple[dict[EntityType, PolicyAction], DeriveAttribute | None]:
    """Given the field currently being extracted, returns exactly what protect() needs to
    apply `config`'s decision for this field -- and nothing else. A field the config does
    not explicitly govern, or whose policy_action_ref cannot be resolved to an EntityType,
    returns an empty entity_actions: resolve_action's own fail-closed Tokenize default
    (Invariant I4) still applies to whatever gets detected, so this never under-protects --
    at worst it protects with Tokenize where the config intended something more nuanced.
    `config_derive_attribute` is the one attribute `config` exposes for its Derive fields
    (there is at most one per config today, since every committed schema's only Derive-
    capable field is pin_code); passed in rather than looked up here, mirroring
    load_and_guard_config's own reasoning for why PolicyConfig itself doesn't carry it.
    """
    action = config.field_actions.get(field_name)
    if action is None:
        return {}, None

    entity_type = _resolve_entity_type(policy_action_ref)
    if entity_type is None:
        return {}, None

    derive_attribute = config_derive_attribute if action is PolicyAction.DERIVE else None
    return {entity_type: action}, derive_attribute
