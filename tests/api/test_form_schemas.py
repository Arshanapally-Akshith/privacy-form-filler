"""HTTP-level tests for `GET /api/form-schemas` (`BUILD.md` Phase 2, task 1/7)."""

from fastapi.testclient import TestClient

from app.api.factory import create_app


def test_list_form_schemas_returns_both_committed_schemas() -> None:
    client = TestClient(create_app())

    response = client.get("/api/form-schemas")

    assert response.status_code == 200
    body = response.json()
    ids = {summary["id"] for summary in body["form_schemas"]}
    assert ids == {"kyc_account_opening", "insurance_policy_application"}
    for summary in body["form_schemas"]:
        assert summary["name"]
        assert summary["description"]
