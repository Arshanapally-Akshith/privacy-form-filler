"""Policy config loading tests (BUILD.md Phase 3, task 7 -- structural half).

Reuses the existing Phase 0 fixtures (tests/fixtures/policy_config/) rather than
duplicating them -- this module validates the same structural contract
tests/test_policy_config_schema.py already covers, just through the typed loader instead
of bare jsonschema.validate(). Nothing here touches the schema or its fixtures.
"""

from pathlib import Path

import pytest

from app.privacy.dispatch import PolicyAction
from app.privacy.policy_config import (
    PolicyConfig,
    PolicyConfigSchemaError,
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
