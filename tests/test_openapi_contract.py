import json
from pathlib import Path

from app.api.factory import create_app

SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "contracts" / "openapi.json"


def test_openapi_schema_matches_committed_snapshot() -> None:
    current_schema = create_app().openapi()
    committed_schema = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert current_schema == committed_schema
