"""HTTP-level tests for POST /api/debug/retrieve (BUILD.md Phase 1, task 6). No real
embedding calls -- vectors are stubbed so behavior is deterministic and offline, per
CLAUDE.md §4. case_index_registry is a process-global singleton, so every test uses a
unique case_id to avoid cross-test interference.
"""

import pytest
from fastapi.testclient import TestClient

from app.api.factory import create_app
from app.ingest.chunker import Chunk
from app.retrieval.embedder import EmbeddingProviderError
from app.retrieval.store import case_index_registry, embed_chunks


def _pan_vector(texts: list[str]) -> list[list[float]]:
    return [[1.0, 0.0] if "pan" in text.lower() else [0.0, 1.0] for text in texts]


def _seed_case(monkeypatch: pytest.MonkeyPatch, case_id: str, chunks: list[Chunk], vector_fn) -> None:
    monkeypatch.setattr("app.retrieval.store.embed_texts", vector_fn)
    case_index_registry.get_or_create(case_id).add(embed_chunks(chunks))


def test_successful_retrieval_returns_ranked_chunks_with_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    case_id = "case-success"
    chunk = Chunk(document_id="doc-1", page_number=3, chunk_index=0, text="PAN: ABCDE1234F")
    _seed_case(monkeypatch, case_id, [chunk], _pan_vector)
    monkeypatch.setattr("app.api.debug.embed_texts", _pan_vector)

    client = TestClient(create_app())
    response = client.post(
        "/api/debug/retrieve",
        json={"case_id": case_id, "field_label": "PAN Number", "top_k": 5},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["case_id"] == case_id
    assert body["field_label"] == "PAN Number"
    assert len(body["chunks"]) == 1
    result = body["chunks"][0]
    assert result["document_id"] == "doc-1"
    assert result["page_number"] == 3
    assert result["chunk_index"] == 0
    assert result["text"] == "PAN: ABCDE1234F"


def test_query_text_reflects_synonym_expansion(monkeypatch: pytest.MonkeyPatch) -> None:
    case_id = "case-query-text"
    chunk = Chunk(document_id="doc-1", page_number=1, chunk_index=0, text="PAN: ABCDE1234F")
    _seed_case(monkeypatch, case_id, [chunk], _pan_vector)
    monkeypatch.setattr("app.api.debug.embed_texts", _pan_vector)

    client = TestClient(create_app())
    response = client.post(
        "/api/debug/retrieve",
        json={"case_id": case_id, "field_label": "PAN Number", "top_k": 5},
    )

    query_text = response.json()["query_text"]
    assert query_text.startswith("PAN Number")
    assert "permanent account number" in query_text.lower()


def test_score_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    case_id = "case-score"
    chunk = Chunk(document_id="doc-1", page_number=1, chunk_index=0, text="PAN: ABCDE1234F")
    _seed_case(monkeypatch, case_id, [chunk], _pan_vector)
    monkeypatch.setattr("app.api.debug.embed_texts", _pan_vector)

    client = TestClient(create_app())
    response = client.post(
        "/api/debug/retrieve",
        json={"case_id": case_id, "field_label": "PAN Number", "top_k": 5},
    )

    score = response.json()["chunks"][0]["score"]
    assert isinstance(score, float)
    assert score == pytest.approx(1.0)


def test_unknown_case_returns_404() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/debug/retrieve",
        json={"case_id": "case-never-created", "field_label": "PAN Number", "top_k": 5},
    )

    assert response.status_code == 404


def test_known_case_with_no_chunks_returns_200_with_empty_results(monkeypatch: pytest.MonkeyPatch) -> None:
    case_id = "case-empty"
    case_index_registry.get_or_create(case_id)  # created, nothing ever added to it
    monkeypatch.setattr("app.api.debug.embed_texts", _pan_vector)

    client = TestClient(create_app())
    response = client.post(
        "/api/debug/retrieve",
        json={"case_id": case_id, "field_label": "PAN Number", "top_k": 5},
    )

    assert response.status_code == 200
    assert response.json()["chunks"] == []


@pytest.mark.parametrize(
    "payload",
    [
        {"case_id": "case-invalid", "field_label": "", "top_k": 5},
        {"case_id": "case-invalid", "field_label": "PAN Number", "top_k": 0},
        {"case_id": "case-invalid", "field_label": "PAN Number", "top_k": -1},
    ],
)
def test_invalid_field_label_or_top_k_returns_422(payload: dict[str, object]) -> None:
    client = TestClient(create_app())
    response = client.post("/api/debug/retrieve", json=payload)

    assert response.status_code == 422


def test_cross_case_isolation_through_http_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    case_a = "case-http-a"
    case_b = "case-http-b"
    chunk_a = Chunk(document_id="doc-a", page_number=1, chunk_index=0, text="shared wording")
    chunk_b = Chunk(document_id="doc-b", page_number=1, chunk_index=0, text="shared wording")

    def _uniform_vector(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    _seed_case(monkeypatch, case_a, [chunk_a], _uniform_vector)
    _seed_case(monkeypatch, case_b, [chunk_b], _uniform_vector)
    monkeypatch.setattr("app.api.debug.embed_texts", _uniform_vector)

    client = TestClient(create_app())
    response = client.post(
        "/api/debug/retrieve",
        json={"case_id": case_a, "field_label": "shared wording", "top_k": 10},
    )

    assert response.status_code == 200
    document_ids = [c["document_id"] for c in response.json()["chunks"]]
    assert document_ids == ["doc-a"]


def test_embedding_provider_failure_returns_502(monkeypatch: pytest.MonkeyPatch) -> None:
    case_id = "case-embed-failure"
    case_index_registry.get_or_create(case_id)

    def _raise(texts: list[str]) -> list[list[float]]:
        raise EmbeddingProviderError("simulated provider outage")

    monkeypatch.setattr("app.api.debug.embed_texts", _raise)

    client = TestClient(create_app())
    response = client.post(
        "/api/debug/retrieve",
        json={"case_id": case_id, "field_label": "PAN Number", "top_k": 5},
    )

    assert response.status_code == 502


def test_endpoint_unavailable_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENABLE_DEBUG_ENDPOINTS", "false")

    client = TestClient(create_app())
    response = client.post(
        "/api/debug/retrieve",
        json={"case_id": "case-disabled", "field_label": "PAN Number", "top_k": 5},
    )

    assert response.status_code == 404
