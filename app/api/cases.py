from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile

from app.api.errors import STANDARD_ERROR_RESPONSES
from app.api.models import (
    CaseStatusResponse,
    CreateCaseResponse,
    FieldRecord,
    FieldsResponse,
    ReviewRequest,
)

router = APIRouter(prefix="/api/cases", tags=["cases"])


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
    raise HTTPException(status_code=501)


@router.get(
    "/{case_id}/status",
    response_model=CaseStatusResponse,
    responses=STANDARD_ERROR_RESPONSES,
)
async def get_case_status(case_id: str) -> CaseStatusResponse:
    raise HTTPException(status_code=501)


@router.get(
    "/{case_id}/fields",
    response_model=FieldsResponse,
    responses=STANDARD_ERROR_RESPONSES,
)
async def get_case_fields(case_id: str) -> FieldsResponse:
    raise HTTPException(status_code=501)


@router.post(
    "/{case_id}/fields/{field_name}/review",
    response_model=FieldRecord,
    responses=STANDARD_ERROR_RESPONSES,
)
async def review_field(case_id: str, field_name: str, review: ReviewRequest) -> FieldRecord:
    raise HTTPException(status_code=501)


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
    raise HTTPException(status_code=501)
