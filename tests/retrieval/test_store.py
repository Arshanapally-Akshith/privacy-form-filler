"""Case-isolation and provenance tests for the per-case vector index (BUILD.md Phase 1,
task 4). No real embedding calls -- vectors are hand-built so behavior is deterministic
and offline, per CLAUDE.md §4.
"""

import pytest

from app.ingest.chunker import Chunk
from app.retrieval.store import CaseIndexRegistry, EmbeddedChunk, embed_chunks


def _embedded(document_id: str, page_number: int, chunk_index: int, text: str, vector: list[float]) -> EmbeddedChunk:
    chunk = Chunk(document_id=document_id, page_number=page_number, chunk_index=chunk_index, text=text)
    return EmbeddedChunk(chunk=chunk, vector=vector)


def test_registry_returns_a_distinct_index_per_case() -> None:
    registry = CaseIndexRegistry()

    index_a = registry.get_or_create("case-a")
    index_b = registry.get_or_create("case-b")

    assert index_a is not index_b

    index_a.add([_embedded("doc-1", 1, 0, "text", [1.0, 0.0])])

    assert len(index_a) == 1
    assert len(index_b) == 0

    # Fetching the same case again returns the same index, not a fresh one.
    assert registry.get_or_create("case-a") is index_a


def test_case_a_query_never_returns_case_b_chunks() -> None:
    registry = CaseIndexRegistry()
    index_a = registry.get_or_create("case-a")
    index_b = registry.get_or_create("case-b")

    index_a.add([_embedded("case-a-doc", 1, 0, "a's content", [1.0, 0.0])])
    index_b.add([_embedded("case-b-doc", 1, 0, "b's content", [1.0, 0.0])])

    results = index_a.query(query_vector=[1.0, 0.0], top_k=10)

    assert len(results) == 1
    assert results[0][1].chunk.document_id == "case-a-doc"
    assert all(embedded.chunk.document_id != "case-b-doc" for _, embedded in results)


def test_identical_content_and_vectors_across_cases_still_respect_isolation() -> None:
    """The adversarial case: two cases hold chunks with identical text and identical
    embedding vectors (e.g. shared boilerplate). Similarity search alone cannot tell them
    apart -- isolation has to come from the two chunks never sharing an index."""
    shared_vector = [1.0, 0.0, 0.0]
    registry = CaseIndexRegistry()
    index_a = registry.get_or_create("case-a")
    index_b = registry.get_or_create("case-b")

    index_a.add([_embedded("case-a-doc", 1, 0, "Boilerplate header text", shared_vector)])
    index_b.add([_embedded("case-b-doc", 1, 0, "Boilerplate header text", shared_vector)])

    results = index_a.query(query_vector=shared_vector, top_k=10)

    assert len(results) == 1
    assert results[0][1].chunk.document_id == "case-a-doc"


def test_embed_chunks_preserves_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = [
        Chunk(document_id="doc-1", page_number=2, chunk_index=0, text="first"),
        Chunk(document_id="doc-1", page_number=2, chunk_index=1, text="second"),
    ]

    def _stub_embed_texts(texts: list[str]) -> list[list[float]]:
        return [[float(i)] for i in range(len(texts))]

    monkeypatch.setattr("app.retrieval.store.embed_texts", _stub_embed_texts)

    embedded = embed_chunks(chunks)

    assert len(embedded) == 2
    assert embedded[0].chunk is chunks[0]
    assert embedded[0].chunk.document_id == "doc-1"
    assert embedded[0].chunk.page_number == 2
    assert embedded[0].chunk.chunk_index == 0
    assert embedded[0].vector == [0.0]
    assert embedded[1].chunk is chunks[1]
    assert embedded[1].chunk.chunk_index == 1
    assert embedded[1].vector == [1.0]


def test_query_ranks_by_similarity_and_respects_top_k() -> None:
    index = CaseIndexRegistry().get_or_create("case-a")
    index.add(
        [
            _embedded("doc-1", 1, 0, "far", [0.0, 1.0]),
            _embedded("doc-1", 1, 1, "near", [1.0, 0.01]),
            _embedded("doc-1", 1, 2, "exact", [1.0, 0.0]),
        ]
    )

    results = index.query(query_vector=[1.0, 0.0], top_k=2)

    assert [embedded.chunk.text for _, embedded in results] == ["exact", "near"]
    scores = [score for score, _ in results]
    assert scores[0] > scores[1]
