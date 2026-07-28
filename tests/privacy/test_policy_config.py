"""Policy config loading and co-occurrence guard tests (BUILD.md Phase 3, task 7).

Reuses the existing Phase 0 fixtures (tests/fixtures/policy_config/) rather than
duplicating them -- this module validates the same structural contract
tests/test_policy_config_schema.py already covers, just through the typed loader instead
of bare jsonschema.validate(). Nothing here touches the schema or its fixtures.

`check_cooccurrence_guard` is tested both against `PolicyConfig` objects constructed
directly (no file I/O needed -- it is a pure function of an already-loaded config plus an
explicit derive_attributes mapping) and, once, against a real fixture file loaded through
`load_policy_config` first, to prove the two-step "load, then guard" call pattern a real
caller (app.privacy.cli, and later the Phase 4 boundary layer) actually uses.
"""

from pathlib import Path

import pytest

from app.privacy.dispatch import DeriveAttribute, PolicyAction
from app.privacy.policy_config import (
    PolicyConfig,
    PolicyConfigCooccurrenceError,
    PolicyConfigMissingDeriveAttributeError,
    PolicyConfigSchemaError,
    check_cooccurrence_guard,
    load_policy_config,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "policy_config"


def test_valid_config_loads_into_typed_model() -> None:
    config = load_policy_config(FIXTURES_DIR / "valid.json")

    assert isinstance(config, PolicyConfig)
    assert config.name == "test_valid_config"
    assert config.dataset_version == "2026-07-26"
    assert config.permitted_cooccurrence_sets == [["age_band", "state"], ["age_band", "city"]]


def test_field_actions_values_are_typed_as_policy_action() -> None:
    config = load_policy_config(FIXTURES_DIR / "valid.json")

    assert config.field_actions["full_name"] is PolicyAction.TOKENIZE
    assert config.field_actions["date_of_birth"] is PolicyAction.GENERALIZE
    assert config.field_actions["pin_code"] is PolicyAction.DERIVE
    assert config.field_actions["form_title"] is PolicyAction.PASS_THROUGH


@pytest.mark.parametrize(
    "fixture_filename",
    [
        "invalid_unknown_action.json",
        "invalid_missing_dataset_version.json",
        "invalid_dataset_version_format.json",
        "invalid_cooccurrence.json",
        "invalid_unknown_top_level_key.json",
    ],
)
def test_invalid_config_raises_policy_config_schema_error(fixture_filename: str) -> None:
    with pytest.raises(PolicyConfigSchemaError):
        load_policy_config(FIXTURES_DIR / fixture_filename)


def test_schema_error_chains_the_underlying_jsonschema_failure() -> None:
    with pytest.raises(PolicyConfigSchemaError) as exc_info:
        load_policy_config(FIXTURES_DIR / "invalid_unknown_action.json")

    assert exc_info.value.__cause__ is not None


# --- check_cooccurrence_guard -------------------------------------------------------------


def _config(
    field_actions: dict[str, PolicyAction], permitted_cooccurrence_sets: list[list[str]]
) -> PolicyConfig:
    return PolicyConfig(
        name="test_config",
        dataset_version="2026-07-26",
        field_actions=field_actions,
        permitted_cooccurrence_sets=permitted_cooccurrence_sets,
    )


def test_guard_passes_when_exposed_combination_is_permitted() -> None:
    config = _config(
        {"date_of_birth": PolicyAction.GENERALIZE, "pin_code": PolicyAction.DERIVE},
        [["age_band", "state"]],
    )
    check_cooccurrence_guard(config, derive_attributes={"pin_code": DeriveAttribute.STATE})


def test_guard_passes_trivially_when_nothing_is_derived_or_generalized() -> None:
    config = _config({"full_name": PolicyAction.TOKENIZE, "annual_income": PolicyAction.PASS_THROUGH}, [])
    check_cooccurrence_guard(config)


def test_guard_raises_when_exposed_combination_is_not_permitted() -> None:
    config = _config(
        {"date_of_birth": PolicyAction.GENERALIZE, "pin_code": PolicyAction.DERIVE},
        [["age_band", "district"]],
    )
    with pytest.raises(PolicyConfigCooccurrenceError):
        check_cooccurrence_guard(config, derive_attributes={"pin_code": DeriveAttribute.STATE})


def test_guard_raises_when_derive_field_has_no_attribute_supplied() -> None:
    config = _config({"pin_code": PolicyAction.DERIVE}, [["state"]])
    with pytest.raises(PolicyConfigMissingDeriveAttributeError):
        check_cooccurrence_guard(config)


def test_guard_raises_when_derive_attribute_supplied_for_a_non_derive_field() -> None:
    config = _config({"full_name": PolicyAction.TOKENIZE}, [])
    with pytest.raises(PolicyConfigMissingDeriveAttributeError):
        check_cooccurrence_guard(config, derive_attributes={"full_name": DeriveAttribute.STATE})


def test_guard_never_inspects_config_name() -> None:
    """The guard must be indifferent to which named config it is given -- passing or
    failing depends only on field_actions/permitted_cooccurrence_sets and the caller-
    supplied derive_attributes, never on `config.name` (e.g. "age_state")."""
    config = _config(
        {"pin_code": PolicyAction.DERIVE}, [["state"]]
    ).model_copy(update={"name": "not_a_real_named_config"})
    check_cooccurrence_guard(config, derive_attributes={"pin_code": DeriveAttribute.STATE})


def test_real_fixture_loads_then_passes_the_guard_via_the_two_step_pattern() -> None:
    """valid.json declares pin_code as Derive; its permitted_cooccurrence_sets offers two
    candidates, [age_band, state] and [age_band, city] -- the caller resolving pin_code to
    STATE matches the first."""
    config = load_policy_config(FIXTURES_DIR / "valid.json")
    check_cooccurrence_guard(config, derive_attributes={"pin_code": DeriveAttribute.STATE})


def test_real_fixture_loads_but_fails_the_guard_when_caller_omits_derive_attribute() -> None:
    config = load_policy_config(FIXTURES_DIR / "valid.json")
    with pytest.raises(PolicyConfigMissingDeriveAttributeError):
        check_cooccurrence_guard(config)


def test_banned_cooccurrence_fixture_loads_but_fails_the_guard() -> None:
    """Structurally valid (loads fine) but semantically banned -- the guard, not the
    schema, is what rejects it."""
    config = load_policy_config(FIXTURES_DIR / "invalid_banned_cooccurrence.json")
    with pytest.raises(PolicyConfigCooccurrenceError):
        check_cooccurrence_guard(config, derive_attributes={"pin_code": DeriveAttribute.STATE})
