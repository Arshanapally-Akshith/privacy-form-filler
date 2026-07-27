"""Per-field retrieval (`ARCHITECTURE.md` §4's "Per-field Retriever" node), made concrete.

Composes the pieces Phase 1 already built -- `build_query_text`, `embed_texts`,
`CaseVectorIndex.query` -- behind a single call so callers (the debug endpoint, and now the
extraction node) depend on `retrieve_for_field()` only, never on the embedder, the query
module, or the vector store's internals directly.
"""

from dataclasses import dataclass

from app.retrieval.embedder import embed_texts
from app.retrieval.query import build_query_text
from app.retrieval.store import case_index_registry


class UnknownCaseError(ValueError):
    """Raised for a `case_id` with no index -- distinct from "known case, no results", so
    callers can fail loudly on a typo'd or never-ingested case rather than silently treating
    it as empty evidence."""


@dataclass(frozen=True)
class RetrievedEvidence:
    document_id: str
    page_number: int
    chunk_index: int
    text: str
    score: float


def retrieve_for_field(case_id: str, field_label: str, top_k: int) -> list[RetrievedEvidence]:
    index = case_index_registry.get(case_id)
    if index is None:
        raise UnknownCaseError(f"Unknown case_id: {case_id!r}")

    query_text = build_query_text(field_label)
    query_vector = embed_texts([query_text])[0]
    results = index.query(query_vector=query_vector, top_k=top_k)

    return [
        RetrievedEvidence(
            document_id=embedded.chunk.document_id,
            page_number=embedded.chunk.page_number,
            chunk_index=embedded.chunk.chunk_index,
            text=embedded.chunk.text,
            score=score,
        )
        for score, embedded in results
    ]
