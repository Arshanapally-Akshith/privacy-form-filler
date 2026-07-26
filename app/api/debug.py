"""Debug retrieval endpoint (BUILD.md Phase 1, task 6).

Dev/evaluation tool only, gated behind Settings.enable_debug_endpoints -- not one of the
six endpoints frozen in the Phase 0 OpenAPI contract. Carries no privacy_mode parameter
and never will: retrieval is mode-invariant per Invariant P1, so this endpoint has nothing
to do with app.privacy and does not import it.

Pure composition of already-tested pieces: field_label -> build_query_text ->
embed_texts -> CaseVectorIndex.query. No ingestion, no parsing, no chunking, and no LLM
calls happen here -- this endpoint only ever queries a case's index, it never populates one.
"""

from fastapi import APIRouter, HTTPException

from app.api.errors import STANDARD_ERROR_RESPONSES
from app.api.models import DebugRetrieveRequest, DebugRetrieveResponse, RetrievedChunk
from app.retrieval.embedder import EmbeddingProviderError, embed_texts
from app.retrieval.query import build_query_text
from app.retrieval.store import case_index_registry

router = APIRouter(prefix="/api/debug", tags=["debug"])


@router.post(
    "/retrieve",
    response_model=DebugRetrieveResponse,
    responses=STANDARD_ERROR_RESPONSES,
)
async def debug_retrieve(request: DebugRetrieveRequest) -> DebugRetrieveResponse:
    index = case_index_registry.get(request.case_id)
    if index is None:
        raise HTTPException(status_code=404, detail=f"Unknown case_id: {request.case_id!r}")

    query_text = build_query_text(request.field_label)

    try:
        query_vector = embed_texts([query_text])[0]
    except EmbeddingProviderError as exc:
        raise HTTPException(status_code=502, detail="Embedding provider request failed") from exc

    results = index.query(query_vector=query_vector, top_k=request.top_k)

    return DebugRetrieveResponse(
        case_id=request.case_id,
        field_label=request.field_label,
        query_text=query_text,
        chunks=[
            RetrievedChunk(
                document_id=embedded.chunk.document_id,
                page_number=embedded.chunk.page_number,
                chunk_index=embedded.chunk.chunk_index,
                text=embedded.chunk.text,
                score=score,
            )
            for score, embedded in results
        ],
    )
