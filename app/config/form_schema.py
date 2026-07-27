"""Form schema format (`BUILD.md` Phase 2, task 1). A form schema describes a blank form's
field definitions -- hand-authored per supported form type (`ARCHITECTURE.md` N1, R9) -- not
data a user submits. Schemas are loaded once, eagerly, so a malformed file fails app start
rather than the first request that happens to touch it (`CLAUDE.md` §5).
"""

import json
import logging
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

logger = logging.getLogger(__name__)

_DEFAULT_FORM_SCHEMA_DIR = Path(__file__).parent / "form_schemas"


class FieldType(str, Enum):
    """`DECISIONS.md` V2 -- the same taxonomy the Phase 6/7 accuracy matrix slices by."""

    IDENTIFIER = "identifier"
    DATE = "date"
    LOCATION = "location"
    NAME = "name"
    NUMERIC_FINANCIAL = "numeric_financial"


class FormSchemaConfigError(ValueError):
    """Raised when a form schema file is structurally invalid, or a set of schema files
    violates a cross-file constraint (e.g. two files sharing an `id`)."""


class FormFieldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    label: str = Field(min_length=1)
    type: FieldType
    required: bool
    expected_format: str | None = None
    # Symbolic reference only (e.g. "pan", "dob") -- deliberately not an enum of privacy
    # actions or detected-entity types. What this key means, and which policy action it maps
    # to, is owned by the policy engine (Phase 3/4). Keeping it an opaque string here means
    # app/config/ never has to import or mirror app/privacy/'s vocabulary.
    policy_action_ref: str | None = None


class FormSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    fields: list[FormFieldSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_unique_field_names(self) -> "FormSchema":
        seen: set[str] = set()
        duplicates: set[str] = set()
        for field in self.fields:
            if field.name in seen:
                duplicates.add(field.name)
            seen.add(field.name)
        if duplicates:
            raise ValueError(
                f"Duplicate field name(s) in form schema {self.id!r}: {sorted(duplicates)}"
            )
        return self


def load_form_schemas(directory: Path = _DEFAULT_FORM_SCHEMA_DIR) -> list[FormSchema]:
    """Load and validate every `*.json` form schema in `directory`. Raises
    `FormSchemaConfigError` on a structurally invalid file or a duplicate `id` across files.
    """
    schemas: list[FormSchema] = []
    seen_ids: dict[str, Path] = {}
    for path in sorted(directory.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        try:
            schema = FormSchema.model_validate(raw)
        except ValidationError as exc:
            raise FormSchemaConfigError(f"Invalid form schema at {path}: {exc}") from exc
        if schema.id in seen_ids:
            raise FormSchemaConfigError(
                f"Duplicate form schema id {schema.id!r}: {seen_ids[schema.id]} and {path}"
            )
        seen_ids[schema.id] = path
        schemas.append(schema)
    return schemas
