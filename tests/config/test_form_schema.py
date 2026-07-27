"""Form schema format contract tests (`BUILD.md` Phase 2, task 1). Written before the
implementation existed, per `CLAUDE.md` §4 -- the contract (name, label, type, required,
expected_format, policy_action_ref) is fully specified in `BUILD.md`, so the test comes
first the same way `test_policy_config_schema.py` did for the policy config in Phase 0.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config.form_schema import FormSchema, FormSchemaConfigError, load_form_schemas

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "form_schemas"
COMMITTED_SCHEMA_IDS = {"kyc_account_opening", "insurance_policy_application"}


def _load_fixture(filename: str) -> dict:
    return json.loads((FIXTURES_DIR / filename).read_text(encoding="utf-8"))


def test_valid_schema_passes() -> None:
    schema = FormSchema.model_validate(_load_fixture("valid.json"))
    assert schema.id == "test_form"
    assert schema.fields[0].name == "full_name"


@pytest.mark.parametrize(
    "fixture_filename",
    [
        "invalid_unknown_field_type.json",
        "invalid_empty_fields.json",
        "invalid_duplicate_field_name.json",
        "invalid_missing_required_key.json",
        "invalid_unknown_top_level_key.json",
    ],
)
def test_invalid_schema_fails(fixture_filename: str) -> None:
    with pytest.raises(ValidationError):
        FormSchema.model_validate(_load_fixture(fixture_filename))


def test_load_form_schemas_loads_the_two_committed_schemas() -> None:
    schemas = load_form_schemas()
    assert {schema.id for schema in schemas} == COMMITTED_SCHEMA_IDS
    for schema in schemas:
        assert len(schema.fields) > 0


def test_load_form_schemas_rejects_duplicate_ids_across_files(tmp_path: Path) -> None:
    schema_a = _load_fixture("valid.json")
    schema_b = {**_load_fixture("valid.json"), "name": "A Different Form"}
    (tmp_path / "a.json").write_text(json.dumps(schema_a), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(schema_b), encoding="utf-8")

    with pytest.raises(FormSchemaConfigError):
        load_form_schemas(directory=tmp_path)


def test_load_form_schemas_reports_path_on_malformed_file(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text(
        json.dumps(_load_fixture("invalid_empty_fields.json")), encoding="utf-8"
    )

    with pytest.raises(FormSchemaConfigError, match="broken.json"):
        load_form_schemas(directory=tmp_path)
