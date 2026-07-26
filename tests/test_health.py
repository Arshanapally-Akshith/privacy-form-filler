from fastapi.testclient import TestClient

from app.api.factory import create_app


def test_health_returns_200() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200


def test_health_returns_json_content_type() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.headers["content-type"] == "application/json"
