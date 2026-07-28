"""Named policy config tests (BUILD.md Phase 3, task 8).

Loads the three named eval configs (ARCH §5.3, DECISIONS.md §2) through the existing,
unmodified Commit 5 loader and drives their declared actions through the existing,
unmodified Commit 5 dispatcher -- no new loading or dispatch code, purely data plus
proof that the data works against what already exists.

Fields with no correct executable action today (NAME/ADDRESS/EMAIL have no FF1 alphabet;
DATE has none either, which matters for `strict` specifically -- see below) are
deliberately absent from every config's `field_actions`, per explicit direction: a config
must not assert an action it cannot execute, and must rely on the dispatcher's existing
fail-closed default for anything it does not explicitly govern. That default is still
Tokenize, which still cannot execute for these types -- an existing, already-documented
engine limitation (Commit 5), not something introduced or hidden by these configs.

`strict` omits `date_of_birth` entirely rather than reassigning it to `generalize`: ARCH
§5.3 defines `strict` as "nothing derived, everything sensitive tokenized," and DOB has no
executable Tokenize path, so the faithful, undisguised state of `strict` is that DOB falls
through to the (currently non-executable) Tokenize default -- not that `strict` quietly
starts generalizing DOB to route around the gap. `age_state`/`ageband_city`, by contrast,
are *defined* by ARCH as exposing "age band" -- Generalize is their correct, intended,
fully-executable action for DOB, not a workaround.
"""

from datetime import date
from pathlib import Path

import pytest

from app.privacy.detection import DetectedEntity, EntityType
from app.privacy.dispatch import (
    DeriveAttribute,
    PolicyAction,
    UnsupportedActionForEntityTypeError,
    apply_action,
    resolve_action,
)
from app.privacy.generalize import generalize_dob
from app.privacy.policy_config import (
    PolicyConfig,
    check_cooccurrence_guard,
    load_policy_config,
)
from app.privacy.tokenize import reverse_entity

CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent / "app" / "config" / "policy_configs"


def _entity(entity_type: EntityType, text: str) -> DetectedEntity:
    return DetectedEntity(entity_type=entity_type, text=text, start=0, end=len(text))


# Fields with no executable action under any config today -- omitted everywhere.
_UNSUPPORTED_FIELDS = ["full_name", "nominee_full_name", "residential_address", "email_address"]


# --- strict ---------------------------------------------------------------------------------


def test_strict_loads() -> None:
    config = load_policy_config(CONFIGS_DIR / "strict.json")
    assert isinstance(config, PolicyConfig)
    assert config.name == "strict"


def test_strict_field_actions_shape() -> None:
    config = load_policy_config(CONFIGS_DIR / "strict.json")

    assert config.field_actions == {
        "pan_number": PolicyAction.TOKENIZE,
        "aadhaar_number": PolicyAction.TOKENIZE,
        "phone_number": PolicyAction.TOKENIZE,
        "pin_code": PolicyAction.TOKENIZE,
        "linked_account_number": PolicyAction.TOKENIZE,
        "annual_income": PolicyAction.PASS_THROUGH,
    }


def test_strict_omits_date_of_birth_rather_than_reassigning_it() -> None:
    config = load_policy_config(CONFIGS_DIR / "strict.json")
    assert "date_of_birth" not in config.field_actions


@pytest.mark.parametrize("field_name", _UNSUPPORTED_FIELDS)
def test_strict_omits_unsupported_fields(field_name: str) -> None:
    config = load_policy_config(CONFIGS_DIR / "strict.json")
    assert field_name not in config.field_actions


def test_strict_permits_nothing_derived_or_generalized() -> None:
    config = load_policy_config(CONFIGS_DIR / "strict.json")
    assert config.permitted_cooccurrence_sets == []


def test_strict_passes_the_cooccurrence_guard() -> None:
    """No Derive field, so no derive_attributes entry is needed -- the guard passes
    trivially, matching its empty permitted_cooccurrence_sets."""
    config = load_policy_config(CONFIGS_DIR / "strict.json")
    check_cooccurrence_guard(config)


def test_strict_tokenize_fields_dispatch_correctly() -> None:
    config = load_policy_config(CONFIGS_DIR / "strict.json")
    cases = {
        "pan_number": (EntityType.PAN, "ABCDE1234F"),
        "aadhaar_number": (EntityType.AADHAAR, "123456789012"),
        "phone_number": (EntityType.PHONE, "9876543210"),
        "pin_code": (EntityType.PIN_CODE, "110001"),
        "linked_account_number": (EntityType.ACCOUNT_NUMBER, "123456789012345"),
    }
    for field_name, (entity_type, value) in cases.items():
        action = config.field_actions[field_name]
        entity = _entity(entity_type, value)
        token = apply_action(action, entity, session_id="s1")
        assert reverse_entity("s1", entity_type, token) == value


def test_strict_pass_through_field_dispatches_correctly() -> None:
    config = load_policy_config(CONFIGS_DIR / "strict.json")
    entity = _entity(EntityType.NAME, "500000")  # value shape is irrelevant to pass-through
    action = config.field_actions["annual_income"]
    assert apply_action(action, entity, session_id="s1") == "500000"


def test_strict_omitted_dob_falls_through_to_fail_closed_default_and_still_cannot_execute() -> None:
    """Documents, rather than hides, the existing engine-level gap (Commit 5): the
    fail-closed default is Tokenize, and Tokenize has no alphabet for DATE. `strict` is
    faithful to ARCH's definition regardless -- this is the engine's limitation, not a
    choice this config makes."""
    resolved = resolve_action(None)
    assert resolved is PolicyAction.TOKENIZE

    entity = _entity(EntityType.DATE, "15/06/1990")
    with pytest.raises(UnsupportedActionForEntityTypeError):
        apply_action(resolved, entity, session_id="s1", reference_date=date(2026, 7, 28))


# --- age_state ------------------------------------------------------------------------------


def test_age_state_loads() -> None:
    config = load_policy_config(CONFIGS_DIR / "age_state.json")
    assert config.name == "age_state"


def test_age_state_field_actions_shape() -> None:
    config = load_policy_config(CONFIGS_DIR / "age_state.json")

    assert config.field_actions == {
        "date_of_birth": PolicyAction.GENERALIZE,
        "pan_number": PolicyAction.TOKENIZE,
        "aadhaar_number": PolicyAction.TOKENIZE,
        "phone_number": PolicyAction.TOKENIZE,
        "pin_code": PolicyAction.DERIVE,
        "linked_account_number": PolicyAction.TOKENIZE,
        "annual_income": PolicyAction.PASS_THROUGH,
    }


@pytest.mark.parametrize("field_name", _UNSUPPORTED_FIELDS)
def test_age_state_omits_unsupported_fields(field_name: str) -> None:
    config = load_policy_config(CONFIGS_DIR / "age_state.json")
    assert field_name not in config.field_actions


def test_age_state_permits_age_band_and_state() -> None:
    config = load_policy_config(CONFIGS_DIR / "age_state.json")
    assert config.permitted_cooccurrence_sets == [["age_band", "state"]]


def test_age_state_passes_the_cooccurrence_guard() -> None:
    config = load_policy_config(CONFIGS_DIR / "age_state.json")
    check_cooccurrence_guard(config, derive_attributes={"pin_code": DeriveAttribute.STATE})


def test_age_state_generalize_dispatches_correctly() -> None:
    config = load_policy_config(CONFIGS_DIR / "age_state.json")
    reference_date = date(2026, 7, 28)
    entity = _entity(EntityType.DATE, "15/06/1990")

    result = apply_action(
        config.field_actions["date_of_birth"], entity, session_id="s1", reference_date=reference_date
    )
    assert result == generalize_dob("15/06/1990", reference_date)


def test_age_state_derive_exposes_state() -> None:
    config = load_policy_config(CONFIGS_DIR / "age_state.json")
    entity = _entity(EntityType.PIN_CODE, "110001")

    result = apply_action(
        config.field_actions["pin_code"],
        entity,
        session_id="s1",
        derive_attribute=DeriveAttribute.STATE,
    )
    assert result == "DELHI"


# --- ageband_city -----------------------------------------------------------------------------


def test_ageband_city_loads_with_the_name_pinned_by_arch_and_decisions() -> None:
    config = load_policy_config(CONFIGS_DIR / "ageband_city.json")
    assert config.name == "ageband_city"


def test_ageband_city_field_actions_matches_age_state() -> None:
    """Expected, not a bug: `field_actions` cannot express *which* derived attribute a
    field exposes (state vs district) -- that requires `derive_attribute`, a caller-
    supplied dispatch parameter deferred to Phase 4 (Commit 5's design). The two configs
    are distinguished by name and `permitted_cooccurrence_sets` only, until then."""
    age_state = load_policy_config(CONFIGS_DIR / "age_state.json")
    ageband_city = load_policy_config(CONFIGS_DIR / "ageband_city.json")
    assert ageband_city.field_actions == age_state.field_actions


def test_ageband_city_permits_age_band_and_district_internally() -> None:
    """The config's own `name` stays "ageband_city" (ARCH §5.3 / DECISIONS.md §2's exact
    naming); "district" is the attribute label used internally, matching derive.py's own
    precedent that the committed dataset provides district, not city."""
    config = load_policy_config(CONFIGS_DIR / "ageband_city.json")
    assert config.permitted_cooccurrence_sets == [["age_band", "district"]]


def test_ageband_city_passes_the_cooccurrence_guard() -> None:
    config = load_policy_config(CONFIGS_DIR / "ageband_city.json")
    check_cooccurrence_guard(config, derive_attributes={"pin_code": DeriveAttribute.DISTRICT})


def test_ageband_city_derive_exposes_district() -> None:
    config = load_policy_config(CONFIGS_DIR / "ageband_city.json")
    entity = _entity(EntityType.PIN_CODE, "110001")

    result = apply_action(
        config.field_actions["pin_code"],
        entity,
        session_id="s1",
        derive_attribute=DeriveAttribute.DISTRICT,
    )
    assert result == "NEW DELHI"
