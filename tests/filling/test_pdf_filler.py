"""PDF filling node tests (BUILD.md Phase 2, task 6).

The I5/G4-continuity test and the two fail-fast validation tests are written first: their
behavior is fully specified by the plan (an abstained field must never render a plausible
value; a caller/schema mismatch and a malformed ExtractionResult must both fail loudly, not
render around the gap). Layout/pagination tests follow, written alongside the
implementation. Assertions are made on round-tripped extracted text, not raw PDF bytes, so
PDF metadata (e.g. an embedded creation timestamp) can't introduce flakiness.
"""

from types import MappingProxyType

import fitz
import pytest

from app.config.form_schema import (
    FieldType,
    FormFieldSpec,
    FormSchema,
    load_form_schemas,
)
from app.extraction.extractor import ExtractionProvenance, ExtractionResult
from app.filling.pdf_filler import (
    FormResultMismatchError,
    IncompleteExtractionResultError,
    fill_form,
)

_FILLED = ExtractionResult(
    value="ABCDE1234F",
    provenance=ExtractionProvenance(document_id="doc-id-proof", page_number=2),
    confidence=0.95,
)
_ABSTAINED = ExtractionResult(value=None, provenance=None, confidence=None)


def _schema(*, fields: list[FormFieldSpec] | None = None) -> FormSchema:
    return FormSchema(
        id="test_form",
        name="Test Form",
        description="A minimal schema for PDF filler tests.",
        fields=fields
        or [
            FormFieldSpec(
                name="pan_number",
                label="PAN Number",
                type=FieldType.IDENTIFIER,
                required=True,
                policy_action_ref="pan",
            )
        ],
    )


def _extract_all_text(pdf_bytes: bytes) -> str:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def test_missing_field_never_renders_a_plausible_value() -> None:
    schema = _schema()
    text = _extract_all_text(fill_form(schema, {"pan_number": _ABSTAINED}))

    assert "ABCDE1234F" not in text
    assert "doc-id-proof" not in text
    assert "Missing" in text
    assert "No supporting evidence found" in text


def test_missing_result_key_raises_form_result_mismatch_error() -> None:
    schema = _schema()
    with pytest.raises(FormResultMismatchError):
        fill_form(schema, {})


def test_unexpected_result_key_raises_form_result_mismatch_error() -> None:
    schema = _schema()
    with pytest.raises(FormResultMismatchError):
        fill_form(schema, {"pan_number": _ABSTAINED, "not_a_real_field": _ABSTAINED})


def test_value_without_provenance_raises_incomplete_extraction_result_error() -> None:
    schema = _schema()
    malformed = ExtractionResult(value="ABCDE1234F", provenance=None, confidence=0.9)

    with pytest.raises(IncompleteExtractionResultError):
        fill_form(schema, {"pan_number": malformed})


def test_filled_field_renders_value_and_provenance() -> None:
    schema = _schema()
    text = _extract_all_text(fill_form(schema, {"pan_number": _FILLED}))

    assert "PAN Number: ABCDE1234F" in text
    assert "Source: doc-id-proof, page 2" in text


def test_field_order_in_output_matches_schema_order() -> None:
    fields = [
        FormFieldSpec(name="a_field", label="A Field", type=FieldType.NAME, required=True, policy_action_ref="name"),
        FormFieldSpec(
            name="b_field", label="B Field", type=FieldType.IDENTIFIER, required=True, policy_action_ref="pan"
        ),
    ]
    schema = _schema(fields=fields)
    results = {"a_field": _ABSTAINED, "b_field": _ABSTAINED}

    text = _extract_all_text(fill_form(schema, results))

    assert text.index("A Field") < text.index("B Field")


def test_accepts_any_mapping_not_just_a_plain_dict() -> None:
    schema = _schema()
    results = MappingProxyType({"pan_number": _ABSTAINED})

    pdf_bytes = fill_form(schema, results)

    assert pdf_bytes.startswith(b"%PDF")


def test_many_fields_overflow_onto_a_second_page() -> None:
    fields = [
        FormFieldSpec(
            name=f"field_{i}", label=f"Field {i}", type=FieldType.IDENTIFIER, required=False, policy_action_ref=None
        )
        for i in range(60)
    ]
    schema = _schema(fields=fields)
    results = {f.name: _ABSTAINED for f in fields}

    pdf_bytes = fill_form(schema, results)

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        assert doc.page_count > 1
    finally:
        doc.close()
    text = _extract_all_text(pdf_bytes)
    assert "Field 0" in text
    assert "Field 59" in text


@pytest.mark.parametrize("schema_id", ["kyc_account_opening", "insurance_policy_application"])
def test_committed_schemas_render_without_error_when_fully_abstained(schema_id: str) -> None:
    schemas = {schema.id: schema for schema in load_form_schemas()}
    schema = schemas[schema_id]
    results = {field.name: _ABSTAINED for field in schema.fields}

    pdf_bytes = fill_form(schema, results)

    assert pdf_bytes.startswith(b"%PDF")
    text = _extract_all_text(pdf_bytes)
    assert schema.name in text
    for field in schema.fields:
        assert field.label in text
