"""PDF filling node with provenance panel (BUILD.md Phase 2, task 6).

Pure output rendering (ARCHITECTURE.md §4.1) -- no decisions made here, no LLM call, no
app.privacy import. The trust-boundary diagram (ARCHITECTURE.md §3) places the filled
output PDF inside the trusted boundary: whatever value an ExtractionResult carries by this
point is already the trusted, real value, independent of privacy_mode.

Depends directly on app.extraction.ExtractionResult -- the same node-to-node dependency
pattern app.retrieval already has on app.ingest.Chunk and app.extraction already has on
app.retrieval.RetrievedEvidence. One-directional, no adapter type: a single concrete
consumer does not justify one (CLAUDE.md §9).

Fail-fast, not silent (CLAUDE.md §5):
- `results` must have exactly one entry per schema field. A missing or unexpected key is a
  caller bug (e.g. Task 7's eventual wiring forgot or mislabeled a field), not a legitimate
  "missing" state, and must fail differently than a genuine abstention.
- A non-abstained ExtractionResult with no provenance violates ExtractionResult's own
  invariant (value/provenance/confidence all None or all set) -- that is a programming
  error upstream, not a state to render around.
"""

from collections.abc import Mapping

import fitz

from app.config.form_schema import FormSchema
from app.extraction.extractor import ExtractionResult
from app.filling.constants import (
    FIELD_SPACING_PT,
    LABEL_FONT_SIZE,
    LABEL_LINE_HEIGHT_PT,
    MARGIN_PT,
    PAGE_HEIGHT_PT,
    PAGE_WIDTH_PT,
    PROVENANCE_FONT_SIZE,
    PROVENANCE_INDENT_PT,
    PROVENANCE_LINE_HEIGHT_PT,
    TITLE_FONT_SIZE,
    TITLE_LINE_HEIGHT_PT,
)

_MISSING_VALUE_PLACEHOLDER = "— Missing —"
_MISSING_PROVENANCE_TEXT = "No supporting evidence found"


class FormResultMismatchError(ValueError):
    """Raised when `results` does not have exactly one entry per form schema field."""


class IncompleteExtractionResultError(ValueError):
    """Raised when a non-abstained `ExtractionResult` has no provenance -- a violation of
    its own value/provenance/confidence invariant, not a legitimate state to render."""


def fill_form(form_schema: FormSchema, results: Mapping[str, ExtractionResult]) -> bytes:
    _validate_results(form_schema, results)

    doc = fitz.open()
    page = doc.new_page(width=PAGE_WIDTH_PT, height=PAGE_HEIGHT_PT)
    y = MARGIN_PT
    y = _insert_line(page, y, form_schema.name, TITLE_FONT_SIZE, TITLE_LINE_HEIGHT_PT)

    for field in form_schema.fields:
        result = results[field.name]
        page, y = _ensure_space(doc, page, y, LABEL_LINE_HEIGHT_PT + PROVENANCE_LINE_HEIGHT_PT)

        if result.value is None:
            value_text = _MISSING_VALUE_PLACEHOLDER
            provenance_text = _MISSING_PROVENANCE_TEXT
        else:
            if result.provenance is None:
                raise IncompleteExtractionResultError(
                    f"Field {field.name!r} has a value but no provenance -- "
                    "ExtractionResult invariant violated upstream"
                )
            value_text = result.value
            provenance_text = (
                f"Source: {result.provenance.document_id}, page {result.provenance.page_number}"
            )

        y = _insert_line(page, y, f"{field.label}: {value_text}", LABEL_FONT_SIZE, LABEL_LINE_HEIGHT_PT)
        y = _insert_line(page, y, provenance_text, PROVENANCE_FONT_SIZE, PROVENANCE_LINE_HEIGHT_PT, indent=True)
        y += FIELD_SPACING_PT

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _validate_results(form_schema: FormSchema, results: Mapping[str, ExtractionResult]) -> None:
    schema_field_names = {field.name for field in form_schema.fields}
    result_keys = set(results.keys())
    missing = schema_field_names - result_keys
    unexpected = result_keys - schema_field_names
    if missing or unexpected:
        raise FormResultMismatchError(
            f"results does not match form schema {form_schema.id!r} fields: "
            f"missing={sorted(missing)}, unexpected={sorted(unexpected)}"
        )


def _insert_line(
    page: fitz.Page, y: float, text: str, font_size: float, line_height: float, indent: bool = False
) -> float:
    x = MARGIN_PT + (PROVENANCE_INDENT_PT if indent else 0.0)
    page.insert_text((x, y), text, fontsize=font_size)
    return y + line_height


def _ensure_space(doc: fitz.Document, page: fitz.Page, y: float, needed_height: float) -> tuple[fitz.Page, float]:
    if y + needed_height > PAGE_HEIGHT_PT - MARGIN_PT:
        page = doc.new_page(width=PAGE_WIDTH_PT, height=PAGE_HEIGHT_PT)
        y = MARGIN_PT
    return page, y
