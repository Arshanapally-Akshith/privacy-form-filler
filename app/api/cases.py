"""Case API endpoints (BUILD.md Phase 2, task 7): wires the Phase 0 contract to the nodes
already built -- ingest/chunk (Phase 1), retrieve + extract (tasks 3-5), fill (task 6).

Synchronous, not background-processed: POST /api/cases blocks until every field has been
extracted and returns a terminal status directly. No queue, no BackgroundTasks, no
orchestration graph -- that machinery belongs to Phase 5 (ARCHITECTURE.md §7), and adding
even a lightweight queued/processing state machine here would be orchestration by another
name.

Two distinct failure classes on case creation:
- Malformed input (unsupported file type, sub-minimum image resolution) is deterministic
  and has nothing to do with an external provider -- rejected outright, 400, no case
  created.
- A provider failure (embedding or LLM) after input is accepted still creates the case
  record and marks it FAILED internally (app.api.case_store, for diagnostics), but the
  HTTP response is 502 -- the caller is never handed a case_id for a resource whose primary
  processing already failed, so this is not a 201.
"""

import logging
import tempfile
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile

from app.api.case_store import CaseRecord, case_store
from app.api.errors import STANDARD_ERROR_RESPONSES
from app.api.models import (
    CaseStatus,
    CaseStatusResponse,
    CreateCaseResponse,
    FieldRecord,
    FieldsResponse,
    FieldState,
    Progress,
    Provenance,
    ReviewDecision,
    ReviewRequest,
)
from app.config.form_schema import FormFieldSpec, FormSchema, load_form_schemas
from app.extraction.constants import DEFAULT_RETRIEVAL_TOP_K
from app.extraction.extractor import (
    ExtractionProvenance,
    ExtractionResult,
    extract_field,
)
from app.extraction.llm_client import LLMProviderError
from app.filling.pdf_filler import fill_form
from app.ingest.chunker import Chunk, chunk_pages
from app.ingest.parser import (
    ImageResolutionError,
    UnsupportedDocumentTypeError,
    parse_document,
)
from app.retrieval.embedder import EmbeddingProviderError
from app.retrieval.store import case_index_registry, embed_chunks

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cases", tags=["cases"])

_FORM_SCHEMAS_BY_ID = {schema.id: schema for schema in load_form_schemas()}
_HUMAN_REVIEW_DOCUMENT_ID = "human_review"
_HUMAN_REVIEW_PAGE_NUMBER = 0


@router.post(
    "",
    response_model=CreateCaseResponse,
    status_code=201,
    responses=STANDARD_ERROR_RESPONSES,
)
async def create_case(
    form_schema_id: Annotated[str, Form()],
    documents: Annotated[list[UploadFile], File()],
) -> CreateCaseResponse:
    schema = _FORM_SCHEMAS_BY_ID.get(form_schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail=f"Unknown form_schema_id: {form_schema_id!r}")

    case_id = uuid4().hex
    chunks = await _ingest_documents(case_id, documents)

    record = case_store.create(case_id, form_schema_id)
    _process_case(record, schema, chunks)

    return CreateCaseResponse(case_id=case_id, form_schema_id=form_schema_id, status=record.status)


@router.get(
    "/{case_id}/status",
    response_model=CaseStatusResponse,
    responses=STANDARD_ERROR_RESPONSES,
)
async def get_case_status(case_id: str) -> CaseStatusResponse:
    record = _get_record_or_404(case_id)
    schema = _FORM_SCHEMAS_BY_ID[record.form_schema_id]
    return CaseStatusResponse(
        case_id=case_id,
        status=record.status,
        progress=Progress(fields_total=len(schema.fields), fields_completed=len(record.fields)),
    )


@router.get(
    "/{case_id}/fields",
    response_model=FieldsResponse,
    responses=STANDARD_ERROR_RESPONSES,
)
async def get_case_fields(case_id: str) -> FieldsResponse:
    record = _get_record_or_404(case_id)
    return FieldsResponse(case_id=case_id, fields=list(record.fields.values()))


@router.post(
    "/{case_id}/fields/{field_name}/review",
    response_model=FieldRecord,
    responses=STANDARD_ERROR_RESPONSES,
)
async def review_field(case_id: str, field_name: str, review: ReviewRequest) -> FieldRecord:
    record = _get_record_or_404(case_id)
    existing = record.fields.get(field_name)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Unknown field_name: {field_name!r} for case {case_id!r}")

    if review.decision == ReviewDecision.APPROVE:
        updated = existing.model_copy(update={"state": FieldState.HUMAN_REVIEWED})
    else:
        normalized_value = (review.value or "").strip()
        if not normalized_value:
            raise HTTPException(status_code=422, detail="OVERRIDE requires a non-empty value")
        updated = FieldRecord(
            name=existing.name,
            label=existing.label,
            value=normalized_value,
            confidence=None,
            state=FieldState.HUMAN_REVIEWED,
            provenance=Provenance(
                document_id=_HUMAN_REVIEW_DOCUMENT_ID, page_number=_HUMAN_REVIEW_PAGE_NUMBER
            ),
        )

    record.fields[field_name] = updated
    return updated


@router.get(
    "/{case_id}/result",
    response_model=None,
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}},
        **STANDARD_ERROR_RESPONSES,
    },
)
async def get_case_result(case_id: str) -> Response:
    record = _get_record_or_404(case_id)
    if record.status != CaseStatus.COMPLETED:
        raise HTTPException(
            status_code=400, detail=f"Case {case_id!r} is not completed (status={record.status.value})"
        )

    schema = _FORM_SCHEMAS_BY_ID[record.form_schema_id]
    results = {name: _to_extraction_result(name, fr) for name, fr in record.fields.items()}
    pdf_bytes = fill_form(schema, results)
    return Response(content=pdf_bytes, media_type="application/pdf")


def _get_record_or_404(case_id: str) -> CaseRecord:
    record = case_store.get(case_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown case_id: {case_id!r}")
    return record


async def _ingest_documents(case_id: str, documents: list[UploadFile]) -> list[Chunk]:
    all_chunks: list[Chunk] = []
    for upload in documents:
        if not upload.filename:
            raise HTTPException(status_code=400, detail="Uploaded document is missing a filename")

        document_id = f"{uuid4().hex[:8]}_{upload.filename}"
        suffix = Path(upload.filename).suffix
        content = await upload.read()

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        try:
            pages = parse_document(tmp_path, document_id)
        except (UnsupportedDocumentTypeError, ImageResolutionError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            tmp_path.unlink(missing_ok=True)

        all_chunks.extend(chunk_pages(pages))
    return all_chunks


def _process_case(record: CaseRecord, schema: FormSchema, chunks: list[Chunk]) -> None:
    try:
        case_index_registry.get_or_create(record.case_id).add(embed_chunks(chunks))
        for schema_field in schema.fields:
            result = extract_field(case_id=record.case_id, field=schema_field, top_k=DEFAULT_RETRIEVAL_TOP_K)
            record.fields[schema_field.name] = _to_field_record(schema_field, result)
    except (EmbeddingProviderError, LLMProviderError) as exc:
        record.status = CaseStatus.FAILED
        logger.exception("case_processing_failed", extra={"case_id": record.case_id})
        raise HTTPException(
            status_code=502, detail=f"Provider request failed while processing case {record.case_id!r}"
        ) from exc

    record.status = CaseStatus.COMPLETED


def _to_field_record(field: FormFieldSpec, result: ExtractionResult) -> FieldRecord:
    if result.value is None:
        return FieldRecord(name=field.name, label=field.label, state=FieldState.MISSING)

    if result.provenance is None:
        raise AssertionError(
            f"ExtractionResult for field {field.name!r} has a value but no provenance -- "
            "ExtractionResult invariant violated upstream"
        )
    return FieldRecord(
        name=field.name,
        label=field.label,
        value=result.value,
        confidence=result.confidence,
        state=FieldState.FILLED,
        provenance=Provenance(document_id=result.provenance.document_id, page_number=result.provenance.page_number),
    )


def _to_extraction_result(field_name: str, record: FieldRecord) -> ExtractionResult:
    if record.value is None:
        return ExtractionResult(value=None, provenance=None, confidence=None)

    if record.provenance is None:
        raise AssertionError(
            f"FieldRecord {field_name!r} has a value but no provenance -- "
            "FieldRecord invariant violated upstream"
        )
    return ExtractionResult(
        value=record.value,
        provenance=ExtractionProvenance(
            document_id=record.provenance.document_id, page_number=record.provenance.page_number
        ),
        confidence=record.confidence,
    )
