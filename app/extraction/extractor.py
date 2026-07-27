"""Extraction node (BUILD.md Phase 2, task 3): for each field, retrieve -> prompt -> parse
structured output. Direct LLM call for now -- the boundary layer arrives in Phase 4
(ARCHITECTURE.md Invariant I3 only applies from Phase 4 onward).

Depends only on app.retrieval.retriever's public contract, not on the embedder, query
module, or vector store directly -- retrieval implementation details stay behind that
module. Likewise depends only on app.extraction.llm_client's provider-neutral contract, not
on the Gemini SDK.

No confidence scoring and no FieldState here -- both are BUILD.md Phase 2 task 4. This
module only ever distinguishes "extracted, with provenance" from "abstained": a field with
no supporting evidence, or whose evidence the model does not consider conclusive, returns
None and is never invented (ARCHITECTURE.md Invariants I5 / G4). A failed LLM or retrieval
call is not caught here and is left to propagate -- CLAUDE.md §5 forbids treating a failed
call as a silent abstention, since the two would otherwise be indistinguishable to a caller.
"""

from dataclasses import dataclass

from pydantic import BaseModel

from app.config.form_schema import FormFieldSpec
from app.extraction.constants import DEFAULT_RETRIEVAL_TOP_K
from app.extraction.llm_client import generate_structured
from app.retrieval.retriever import RetrievedEvidence, retrieve_for_field


class _FieldExtractionResponse(BaseModel):
    value: str | None
    source_chunk_index: int | None


@dataclass(frozen=True)
class ExtractionProvenance:
    document_id: str
    page_number: int


@dataclass(frozen=True)
class ExtractionResult:
    """`value` and `provenance` are both `None` together (abstention) or both set."""

    value: str | None
    provenance: ExtractionProvenance | None


_ABSTAINED = ExtractionResult(value=None, provenance=None)


def extract_field(
    case_id: str, field: FormFieldSpec, top_k: int = DEFAULT_RETRIEVAL_TOP_K
) -> ExtractionResult:
    evidence = retrieve_for_field(case_id=case_id, field_label=field.label, top_k=top_k)
    if not evidence:
        return _ABSTAINED

    parsed = generate_structured(_build_prompt(field, evidence), _FieldExtractionResponse)

    if parsed.value is None or parsed.source_chunk_index is None:
        return _ABSTAINED
    if not 0 <= parsed.source_chunk_index < len(evidence):
        # The model cited a chunk outside what was actually retrieved -- an ungrounded
        # answer cannot be trusted with provenance it doesn't have. Abstain (I5), don't guess.
        return _ABSTAINED

    source = evidence[parsed.source_chunk_index]
    return ExtractionResult(
        value=parsed.value,
        provenance=ExtractionProvenance(document_id=source.document_id, page_number=source.page_number),
    )


def _build_prompt(field: FormFieldSpec, evidence: list[RetrievedEvidence]) -> str:
    evidence_block = "\n\n".join(
        f"[chunk {i}] (document={item.document_id}, page={item.page_number})\n{item.text}"
        for i, item in enumerate(evidence)
    )
    format_hint = f" Expected format: {field.expected_format}." if field.expected_format else ""
    return (
        "You are extracting a single field's value from evidence excerpts drawn from a "
        "user's uploaded documents. Use only the evidence below -- never infer, guess, or "
        "use outside knowledge.\n\n"
        f"Field: {field.label}.{format_hint}\n\n"
        f"Evidence:\n{evidence_block}\n\n"
        "If and only if the evidence clearly and directly states this field's value, "
        "respond with that value and the index of the chunk that supports it "
        "(source_chunk_index). If the evidence does not contain this field's value, "
        "respond with value=null and source_chunk_index=null. Never invent a plausible "
        "value that is not directly stated in the evidence."
    )
