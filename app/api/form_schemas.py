from fastapi import APIRouter, HTTPException

from app.api.errors import STANDARD_ERROR_RESPONSES
from app.api.models import FormSchemasResponse

router = APIRouter(prefix="/api/form-schemas", tags=["form-schemas"])


@router.get(
    "",
    response_model=FormSchemasResponse,
    responses=STANDARD_ERROR_RESPONSES,
)
async def list_form_schemas() -> FormSchemasResponse:
    raise HTTPException(status_code=501)
