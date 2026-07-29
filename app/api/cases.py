"""Case API endpoints (BUILD.md Phase 2, task 7): wires the Phase 0 contract to the nodes
already built -- ingest/chunk (Phase 1), retrieve + extract (tasks 3-5), fill (task 6).

Synchronous, not background-processed: POST /api/cases blocks until every field has been
extracted and returns a terminal status directly. No queue, no BackgroundTasks -- field
processing itself is delegated to the Phase 5 orchestration graph
(app.orchestration.graph.run_graph, BUILD.md Phase 5 commit 4), but this handler still
waits for it to finish and returns a terminal status synchronously, same as before that
graph existed. That terminal status is COMPLETED or, since BUILD.md Phase 5 commit 9,
HUMAN_REVIEW -- whenever the graph leaves any field CONFLICT or LOW_CONFIDENCE
(_recompute_case_status). review_field recomputes the same way after every review, so a
case reverts to COMPLETED the moment its last flagged field is reviewed; neither this
handler nor review_field ever resumes or re-enters the graph itself -- by the time either
runs, the graph has already terminated for good.

Three distinct failure classes on case creation:
- Malformed input (unsupported file type, sub-minimum image resolution, an unsupported
  privacy_mode) is deterministic and has nothing to do with an external provider or
  document content -- rejected outright, no case created. privacy_mode=policy_engine is
  rejected here unconditionally: BUILD.md Phase 4 commit 6 made it fully functional at the
  boundary layer (app.boundary.policy_engine), but selecting it through this API is a
  separate, not-yet-made decision this endpoint's own request contract (frozen at commit 5)
  does not expose yet -- rejecting it here is deliberate, not a placeholder for a gap.
- A provider failure (embedding or LLM) after input is accepted still creates the case
  record and marks it FAILED internally (app.api.case_store, for diagnostics), but the
  HTTP response is 502 -- the caller is never handed a case_id for a resource whose primary
  processing already failed, so this is not a 201.
- A privacy-engine dispatch failure (e.g. privacy_mode=full_tokenize hitting an entity type
  Tokenize cannot execute -- an existing, already-documented Phase 3 gap, ARCH §5.1/§11)
  is content-dependent, not something request-time validation can rule out. Same
  FAILED/502-shaped treatment as a provider failure, but reported as 500 -- this is not an
  upstream provider outage, it is our own engine's known limitation.
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
from app.boundary.mode import PrivacyMode
from app.config.form_schema import FormSchema, load_form_schemas
from app.extraction.extractor import ExtractionProvenance, ExtractionResult
from app.extraction.llm_client import LLMProviderError
from app.filling.pdf_filler import fill_form
from app.ingest.chunker import Chunk, chunk_pages
from app.ingest.parser import (
    ImageResolutionError,
    UnsupportedDocumentTypeError,
    parse_document,
)
from app.orchestration.graph import run_graph
from app.orchestration.state import new_orchestration_state
from app.privacy.dispatch import PolicyDispatchError
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
    privacy_mode: Annotated[PrivacyMode, Form()] = PrivacyMode.NONE,
) -> CreateCaseResponse:
    schema = _FORM_SCHEMAS_BY_ID.get(form_schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail=f"Unknown form_schema_id: {form_schema_id!r}")
    if privacy_mode is PrivacyMode.POLICY_ENGINE:
        # Functional at the boundary layer since BUILD.md Phase 4 commit 6
        # (app.boundary.policy_engine) -- rejected here regardless, because letting a
        # caller actually select it is a request-contract decision this endpoint (frozen at
        # commit 5) does not make yet, not a placeholder for a missing implementation.
        # Rejected before any document is touched, same as the schema check above:
        # deterministic, request-time, unrelated to content.
        raise HTTPException(
            status_code=501, detail="privacy_mode=policy_engine is not yet supported"
        )

    case_id = uuid4().hex
    chunks = await _ingest_documents(case_id, documents)

    record = case_store.create(case_id, form_schema_id, privacy_mode=privacy_mode)
    _process_case(record, schema, chunks)

    return CreateCaseResponse(
        case_id=case_id, form_schema_id=form_schema_id, status=record.status, privacy_mode=privacy_mode
    )


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
    record.status = _recompute_case_status(record)
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
        state = new_orchestration_state(
            case_id=record.case_id,
            form_schema=schema,
            privacy_mode=record.privacy_mode,
            reference_date=record.submitted_at,
        )
        final_state = run_graph(state)
        for field_name, field_state in final_state.fields.items():
            record.fields[field_name] = field_state.record
    except (EmbeddingProviderError, LLMProviderError) as exc:
        record.status = CaseStatus.FAILED
        logger.exception("case_processing_failed", extra={"case_id": record.case_id})
        raise HTTPException(
            status_code=502, detail=f"Provider request failed while processing case {record.case_id!r}"
        ) from exc
    except PolicyDispatchError as exc:
        # TODO(BUILD.md Phase 4 commit 6+): temporary. app.privacy.dispatch's exceptions
        # are caught directly here because nothing in the boundary layer translates them
        # yet (app.boundary.payload.protect() deliberately lets them propagate unchanged --
        # see its own docstring). Once the boundary owns a translation layer for privacy
        # implementation failures, this API-level handler should catch a boundary-owned
        # exception instead of reaching past app.boundary into app.privacy directly.
        record.status = CaseStatus.FAILED
        logger.exception("case_processing_failed", extra={"case_id": record.case_id})
        raise HTTPException(
            status_code=500,
            detail=f"Privacy engine could not process case {record.case_id!r} under the active privacy_mode",
        ) from exc

    record.status = _recompute_case_status(record)


_FLAGGED_FIELD_STATES = frozenset({FieldState.CONFLICT, FieldState.LOW_CONFIDENCE})


def _recompute_case_status(record: CaseRecord) -> CaseStatus:
    """The graph (`app.orchestration`) has already terminated by the time this runs --
    called once after `_process_case` finishes populating `record.fields`, and again after
    every `review_field` call. Neither caller resumes or re-enters the graph; this function
    only derives a case-level status from the field states already present, which is what
    keeps it deterministic and idempotent regardless of how many times or in what order
    fields get reviewed.

    `ARCH §7`'s routing table has no case-level status of its own -- `FieldState.CONFLICT`
    (verifier escalation) and `FieldState.LOW_CONFIDENCE` (retry budget exhausted, BUILD.md
    Phase 5 commit 6) are the two field states that mean "needs a human," so a case needs
    human review exactly when at least one field is still in either. `HUMAN_REVIEW` reverts
    to `COMPLETED` the moment the last flagged field is reviewed, since `review_field`
    unconditionally sets a reviewed field's state to `HUMAN_REVIEWED` -- never CONFLICT or
    LOW_CONFIDENCE -- so a fully-reviewed case can never re-trigger this branch on its own.
    """
    if any(field.state in _FLAGGED_FIELD_STATES for field in record.fields.values()):
        return CaseStatus.HUMAN_REVIEW
    return CaseStatus.COMPLETED


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
