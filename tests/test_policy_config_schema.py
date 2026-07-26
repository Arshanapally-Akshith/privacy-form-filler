import json
from pathlib import Path

import jsonschema
import pytest

SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent / "app" / "config" / "policy_config.schema.json"
)
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "policy_config"

SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _load_fixture(filename: str) -> dict:
    return json.loads((FIXTURES_DIR / filename).read_text(encoding="utf-8"))


def test_valid_config_passes() -> None:
    jsonschema.validate(instance=_load_fixture("valid.json"), schema=SCHEMA)


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
def test_invalid_config_fails(fixture_filename: str) -> None:
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=_load_fixture(fixture_filename), schema=SCHEMA)
