from fastapi import APIRouter

from app.api.errors import STANDARD_ERROR_RESPONSES
from app.api.models import FormSchemasResponse, FormSchemaSummary
from app.config.form_schema import load_form_schemas

router = APIRouter(prefix="/api/form-schemas", tags=["form-schemas"])

# Loaded once at import time so a malformed schema file fails app start, not first request.
_FORM_SCHEMAS = load_form_schemas()


@router.get(
    "",
    response_model=FormSchemasResponse,
    responses=STANDARD_ERROR_RESPONSES,
)
async def list_form_schemas() -> FormSchemasResponse:
    return FormSchemasResponse(
        form_schemas=[
            FormSchemaSummary(id=schema.id, name=schema.name, description=schema.description)
            for schema in _FORM_SCHEMAS
        ]
    )
