"""Per-case vector index (BUILD.md Phase 1, task 4).

In-process, in-memory, brute-force cosine similarity -- no FAISS/ANN index, no external
vector database, no numpy (DECISIONS.md E15). Per-case corpora at this project's scale are
tens to low hundreds of chunks; an approximate-nearest-neighbor structure would add a
dependency and complexity without a measurable benefit at that volume.

Case isolation (ARCHITECTURE.md Invariant I6, DECISIONS.md R4) is structural, not
filter-based: a CaseVectorIndex holds only what was added to it directly and has no
concept of "another case." CaseIndexRegistry is the only place a case_id is ever
associated with an index, so there is no shared index and no filter to forget.

Lifecycle (DECISIONS.md R14): in-memory, per-process, not durable across restarts,
consistent with R8. A container restart loses all indices; cases must be re-ingested.
Chosen to avoid persistence-layer complexity not required by Phase 1.
"""

import math
from dataclasses import dataclass

from app.ingest.chunker import Chunk
from app.retrieval.embedder import embed_texts


@dataclass(frozen=True)
class EmbeddedChunk:
    chunk: Chunk
    vector: list[float]


def embed_chunks(chunks: list[Chunk]) -> list[EmbeddedChunk]:
    """Embeds each chunk's text and pairs it back with its source chunk, preserving
    document_id/page_number/chunk_index provenance end to end."""
    vectors = embed_texts([chunk.text for chunk in chunks])
    return [EmbeddedChunk(chunk=chunk, vector=vector) for chunk, vector in zip(chunks, vectors)]


class CaseVectorIndex:
    """Holds embedded chunks for a single case. Has no notion of case_id or of any other
    case -- isolation comes from never receiving another case's data, not from filtering
    it out at query time."""

    def __init__(self) -> None:
        self._entries: list[EmbeddedChunk] = []

    def add(self, embedded_chunks: list[EmbeddedChunk]) -> None:
        self._entries.extend(embedded_chunks)

    def query(self, query_vector: list[float], top_k: int) -> list[tuple[float, EmbeddedChunk]]:
        scored = [(_cosine_similarity(query_vector, entry.vector), entry) for entry in self._entries]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored[:top_k]

    def __len__(self) -> int:
        return len(self._entries)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot_product = sum(x * y for x, y in zip(a, b))
    magnitude_a = math.sqrt(sum(x * x for x in a))
    magnitude_b = math.sqrt(sum(y * y for y in b))
    if magnitude_a == 0.0 or magnitude_b == 0.0:
        return 0.0
    return dot_product / (magnitude_a * magnitude_b)


class CaseIndexRegistry:
    """The only place a case_id is ever associated with a CaseVectorIndex."""

    def __init__(self) -> None:
        self._indices: dict[str, CaseVectorIndex] = {}

    def get_or_create(self, case_id: str) -> CaseVectorIndex:
        if case_id not in self._indices:
            self._indices[case_id] = CaseVectorIndex()
        return self._indices[case_id]

    def get(self, case_id: str) -> CaseVectorIndex | None:
        """Non-creating lookup. Returns None for a case_id that was never populated, so
        callers (e.g. the debug retrieval endpoint) can distinguish "unknown case" from
        "known case, nothing indexed yet" instead of silently manufacturing an empty index
        for a typo'd id."""
        return self._indices.get(case_id)


# Process-global, in-memory registry (DECISIONS.md R14). Whatever populates a case's
# index (currently: direct Python calls from tests/harnesses -- see DECISIONS.md notes on
# app/api/debug.py) and whatever queries it (the debug retrieval endpoint) share this same
# instance.
case_index_registry = CaseIndexRegistry()
