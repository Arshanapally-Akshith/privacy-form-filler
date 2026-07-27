"""Unit tests for `app.retrieval.retriever` (BUILD.md Phase 2, task 3 -- extracted from the
Phase 1 debug endpoint so the extraction node can share it). Behavior parity with the debug
endpoint is covered by `tests/api/test_debug_retrieve.py`; these tests exercise the module
directly. No real embedding calls, per CLAUDE.md §4.
"""

import pytest

from app.ingest.chunker import Chunk
from app.retrieval.retriever import UnknownCaseError, retrieve_for_field
from app.retrieval.store import case_index_registry, embed_chunks


def _pan_vector(texts: list[str]) -> list[list[float]]:
    return [[1.0, 0.0] if "pan" in text.lower() else [0.0, 1.0] for text in texts]


def test_retrieve_for_field_returns_ranked_evidence_with_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    case_id = "retriever-case-success"
    chunk = Chunk(document_id="doc-1", page_number=3, chunk_index=0, text="PAN: ABCDE1234F")
    monkeypatch.setattr("app.retrieval.store.embed_texts", _pan_vector)
    case_index_registry.get_or_create(case_id).add(embed_chunks([chunk]))
    monkeypatch.setattr("app.retrieval.retriever.embed_texts", _pan_vector)

    evidence = retrieve_for_field(case_id=case_id, field_label="PAN Number", top_k=5)

    assert len(evidence) == 1
    assert evidence[0].document_id == "doc-1"
    assert evidence[0].page_number == 3
    assert evidence[0].chunk_index == 0
    assert evidence[0].text == "PAN: ABCDE1234F"
    assert evidence[0].score == pytest.approx(1.0)


def test_retrieve_for_field_raises_for_unknown_case() -> None:
    with pytest.raises(UnknownCaseError):
        retrieve_for_field(case_id="retriever-case-never-created", field_label="PAN Number", top_k=5)
