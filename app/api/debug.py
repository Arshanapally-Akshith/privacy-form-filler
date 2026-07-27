"""Debug retrieval endpoint (BUILD.md Phase 1, task 6).

Dev/evaluation tool only, gated behind Settings.enable_debug_endpoints -- not one of the
six endpoints frozen in the Phase 0 OpenAPI contract. Carries no privacy_mode parameter
and never will: retrieval is mode-invariant per Invariant P1, so this endpoint has nothing
to do with app.privacy and does not import it.

Thin HTTP wrapper over `app.retrieval.retriever.retrieve_for_field` -- the actual
field_label -> build_query_text -> embed_texts -> CaseVectorIndex.query composition lives
there so the extraction node (BUILD.md Phase 2, task 3) can share it. No ingestion, no
parsing, no chunking, and no LLM calls happen here -- this endpoint only ever queries a
case's index, it never populates one.
"""

from fastapi import APIRouter, HTTPException

from app.api.errors import STANDARD_ERROR_RESPONSES
from app.api.models import DebugRetrieveRequest, DebugRetrieveResponse, RetrievedChunk
from app.retrieval.embedder import EmbeddingProviderError
from app.retrieval.query import build_query_text
from app.retrieval.retriever import UnknownCaseError, retrieve_for_field

router = APIRouter(prefix="/api/debug", tags=["debug"])


@router.post(
    "/retrieve",
    response_model=DebugRetrieveResponse,
    responses=STANDARD_ERROR_RESPONSES,
)
async def debug_retrieve(request: DebugRetrieveRequest) -> DebugRetrieveResponse:
    try:
        evidence = retrieve_for_field(
            case_id=request.case_id, field_label=request.field_label, top_k=request.top_k
        )
    except UnknownCaseError:
        raise HTTPException(status_code=404, detail=f"Unknown case_id: {request.case_id!r}") from None
    except EmbeddingProviderError as exc:
        raise HTTPException(status_code=502, detail="Embedding provider request failed") from exc

    return DebugRetrieveResponse(
        case_id=request.case_id,
        field_label=request.field_label,
        query_text=build_query_text(request.field_label),
        chunks=[
            RetrievedChunk(
                document_id=item.document_id,
                page_number=item.page_number,
                chunk_index=item.chunk_index,
                text=item.text,
                score=item.score,
            )
            for item in evidence
        ],
    )
