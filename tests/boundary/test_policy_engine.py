"""Tests for app.boundary.policy_engine (BUILD.md Phase 4, commit 6).

resolve_field_policy is a pure function of an explicit config argument, so it is exercised
directly against all three real named configs (strict, age_state, ageband_city) here --
proving the mechanism generalizes, not just that the one config wired live
(ACTIVE_POLICY_CONFIG) happens to work. app.boundary.test_llm.py separately covers the
live integration through generate_structured_protected.
"""

from pathlib import Path

import pytest

from app.boundary import policy_engine
from app.privacy.detection import EntityType
from app.privacy.dispatch import DeriveAttribute, PolicyAction
from app.privacy.policy_config import PolicyConfig, load_policy_config

CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent / "app" / "config" / "policy_configs"


def _load(config_name: str) -> PolicyConfig:
    return load_policy_config(CONFIGS_DIR / f"{config_name}.json")


# --- module-level active config (loaded eagerly at import time) --------------------------


def test_active_policy_config_is_age_state() -> None:
    assert policy_engine.ACTIVE_POLICY_CONFIG.name == "age_state"


def test_active_policy_config_derive_attribute_is_state() -> None:
    assert policy_engine.ACTIVE_POLICY_CONFIG_DERIVE_ATTRIBUTE is DeriveAttribute.STATE


def test_load_and_guard_config_passes_for_every_named_config() -> None:
    """Proves the guard integration holds generically, not just for the one config wired
    live -- reuses the same function ACTIVE_POLICY_CONFIG's own module-level load used."""
    for name in ("strict", "age_state", "ageband_city"):
        config = policy_engine.load_and_guard_config(name)
        assert config.name == name


# --- resolve_field_policy: tokenize -------------------------------------------------------


def test_resolve_field_policy_tokenize_under_strict() -> None:
    config = _load("strict")
    entity_actions, derive_attribute = policy_engine.resolve_field_policy(
        "pan_number", "pan", config, None
    )
    assert entity_actions == {EntityType.PAN: PolicyAction.TOKENIZE}
    assert derive_attribute is None


# --- resolve_field_policy: generalize ------------------------------------------------------


def test_resolve_field_policy_generalize_under_age_state() -> None:
    config = _load("age_state")
    entity_actions, derive_attribute = policy_engine.resolve_field_policy(
        "date_of_birth", "dob", config, DeriveAttribute.STATE
    )
    assert entity_actions == {EntityType.DATE: PolicyAction.GENERALIZE}
    assert derive_attribute is None  # not a Derive field -- the config's attribute is irrelevant here


# --- resolve_field_policy: derive, per config ----------------------------------------------


def test_resolve_field_policy_derive_under_age_state_exposes_state() -> None:
    config = _load("age_state")
    entity_actions, derive_attribute = policy_engine.resolve_field_policy(
        "pin_code", "pincode", config, DeriveAttribute.STATE
    )
    assert entity_actions == {EntityType.PIN_CODE: PolicyAction.DERIVE}
    assert derive_attribute is DeriveAttribute.STATE


def test_resolve_field_policy_derive_under_ageband_city_exposes_district() -> None:
    config = _load("ageband_city")
    entity_actions, derive_attribute = policy_engine.resolve_field_policy(
        "pin_code", "pincode", config, DeriveAttribute.DISTRICT
    )
    assert entity_actions == {EntityType.PIN_CODE: PolicyAction.DERIVE}
    assert derive_attribute is DeriveAttribute.DISTRICT


# --- resolve_field_policy: fields the config does not govern --------------------------------


def test_resolve_field_policy_returns_empty_for_a_field_absent_from_field_actions() -> None:
    """full_name is not in strict.json's field_actions -- resolve_action's own fail-closed
    default (Invariant I4) is what governs it, not this function."""
    config = _load("strict")
    entity_actions, derive_attribute = policy_engine.resolve_field_policy(
        "full_name", "name", config, None
    )
    assert entity_actions == {}
    assert derive_attribute is None


def test_resolve_field_policy_returns_empty_when_policy_action_ref_is_none() -> None:
    """annual_income has a field_actions entry (pass_through) but no policy_action_ref --
    nothing gets detected for its content anyway, so no override is needed or produced."""
    config = _load("strict")
    assert config.field_actions["annual_income"] is PolicyAction.PASS_THROUGH
    entity_actions, derive_attribute = policy_engine.resolve_field_policy(
        "annual_income", None, config, None
    )
    assert entity_actions == {}
    assert derive_attribute is None


def test_resolve_field_policy_returns_empty_when_policy_action_ref_is_unresolvable() -> None:
    config = _load("strict")
    entity_actions, derive_attribute = policy_engine.resolve_field_policy(
        "some_field", "not_a_real_ref", config, None
    )
    assert entity_actions == {}
    assert derive_attribute is None


# --- policy_action_ref -> EntityType translation --------------------------------------------


@pytest.mark.parametrize(
    ("policy_action_ref", "expected"),
    [
        ("name", EntityType.NAME),
        ("pan", EntityType.PAN),
        ("aadhaar", EntityType.AADHAAR),
        ("address", EntityType.ADDRESS),
        ("phone", EntityType.PHONE),
        ("email", EntityType.EMAIL),
        ("account_number", EntityType.ACCOUNT_NUMBER),
        ("dob", EntityType.DATE),  # renamed override
        ("pincode", EntityType.PIN_CODE),  # renamed override
    ],
)
def test_resolve_entity_type_covers_every_policy_action_ref_used_by_the_real_schemas(
    policy_action_ref: str, expected: EntityType
) -> None:
    assert policy_engine._resolve_entity_type(policy_action_ref) is expected
