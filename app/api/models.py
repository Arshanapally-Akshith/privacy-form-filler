from enum import Enum

from pydantic import BaseModel, Field


class FieldState(str, Enum):
    FILLED = "filled"
    LOW_CONFIDENCE = "low_confidence"
    CONFLICT = "conflict"
    MISSING = "missing"
    HUMAN_REVIEWED = "human_reviewed"


class CaseStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    HUMAN_REVIEW = "human_review"
    COMPLETED = "completed"
    FAILED = "failed"


class ReviewDecision(str, Enum):
    APPROVE = "approve"
    OVERRIDE = "override"


class Provenance(BaseModel):
    document_id: str
    page_number: int


class FieldRecord(BaseModel):
    name: str
    label: str
    value: str | None = None
    confidence: float | None = None
    state: FieldState
    provenance: Provenance | None = None


class CreateCaseResponse(BaseModel):
    case_id: str
    form_schema_id: str
    status: CaseStatus


class Progress(BaseModel):
    fields_total: int
    fields_completed: int


class CaseStatusResponse(BaseModel):
    case_id: str
    status: CaseStatus
    progress: Progress


class FieldsResponse(BaseModel):
    case_id: str
    fields: list[FieldRecord]


class ReviewRequest(BaseModel):
    decision: ReviewDecision
    value: str | None = None


class FormSchemaSummary(BaseModel):
    id: str
    name: str
    description: str


class FormSchemasResponse(BaseModel):
    form_schemas: list[FormSchemaSummary]


class DebugRetrieveRequest(BaseModel):
    case_id: str
    field_label: str = Field(min_length=1)
    top_k: int = Field(gt=0)


class RetrievedChunk(BaseModel):
    document_id: str
    page_number: int
    chunk_index: int
    text: str
    score: float


class DebugRetrieveResponse(BaseModel):
    case_id: str
    field_label: str
    query_text: str
    chunks: list[RetrievedChunk]
