"""Query construction tests (BUILD.md Phase 1, task 5)."""

import pytest

from app.ingest.chunker import Chunk
from app.retrieval.query import build_query_text
from app.retrieval.store import CaseIndexRegistry, embed_chunks


def test_unknown_label_returns_unchanged_text() -> None:
    assert build_query_text("Nationality") == "Nationality"


def test_known_synonym_expansion_appends_terms() -> None:
    result = build_query_text("PAN Number")

    assert result.startswith("PAN Number")
    assert "permanent account number" in result.lower()


def test_matching_is_case_insensitive() -> None:
    result = build_query_text("dob")

    assert result.startswith("dob")
    assert "date of birth" in result.lower()


def test_output_is_deterministic() -> None:
    assert build_query_text("Date of Birth") == build_query_text("Date of Birth")


def test_original_label_casing_and_wording_is_preserved() -> None:
    result = build_query_text("Applicant's PAN")

    assert result.startswith("Applicant's PAN")


def test_end_to_end_query_composition_returns_expected_chunk_with_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """label -> build_query_text -> (stubbed) embed_texts -> CaseVectorIndex.query
    -> expected chunk returned with provenance intact."""
    target_chunk = Chunk(document_id="doc-1", page_number=3, chunk_index=0, text="PAN: ABCDE1234F")
    other_chunk = Chunk(document_id="doc-1", page_number=1, chunk_index=0, text="Cover page, no identifiers here")

    def _stub_embed_texts(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if "pan" in text.lower() else [0.0, 1.0] for text in texts]

    monkeypatch.setattr("app.retrieval.store.embed_texts", _stub_embed_texts)

    index = CaseIndexRegistry().get_or_create("case-1")
    index.add(embed_chunks([target_chunk, other_chunk]))

    query_text = build_query_text("PAN Number")
    query_vector = _stub_embed_texts([query_text])[0]

    results = index.query(query_vector=query_vector, top_k=1)

    assert len(results) == 1
    _, embedded = results[0]
    assert embedded.chunk.document_id == "doc-1"
    assert embedded.chunk.page_number == 3
    assert embedded.chunk.text == "PAN: ABCDE1234F"
