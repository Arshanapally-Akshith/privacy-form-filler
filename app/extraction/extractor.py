"""Extraction node (BUILD.md Phase 2, tasks 3-5): for each field, retrieve -> prompt ->
parse structured output, including a confidence score and a hardened abstention path.
Direct LLM call for now -- the boundary layer arrives in Phase 4
(ARCHITECTURE.md Invariant I3 only applies from Phase 4 onward).

Depends only on app.retrieval.retriever's public contract, not on the embedder, query
module, or vector store directly -- retrieval implementation details stay behind that
module. Likewise depends only on app.extraction.llm_client's provider-neutral contract, not
on the Gemini SDK.

Confidence (DECISIONS.md E7, BUILD.md Phase 2 task 4) lives entirely here, not in
llm_client or retriever: it is the LLM's own self-assessment, elicited in the same
structured-output call as the extraction, of how directly and unambiguously the cited
evidence supports the value. llm_client stays a generic (prompt, schema) -> T with no
notion of "confidence"; retriever's similarity score stays a retrieval-quality signal, not
folded into this number.

No FieldState here -- that is wiring (BUILD.md Phase 2 task 7) or the verifier (Phase 5).
This module only ever distinguishes "extracted, with provenance and confidence" from
"abstained": a field with no supporting evidence, or whose evidence the model does not
consider conclusive, returns None and is never invented (ARCHITECTURE.md Invariants I5 /
G4). A failed LLM or retrieval call is not caught here and is left to propagate --
CLAUDE.md §5 forbids treating a failed call as a silent abstention, since the two would
otherwise be indistinguishable to a caller.

Abstention path (BUILD.md Phase 2 task 5, CLAUDE.md protected Invariant I5): a returned
value is normalized (trimmed) exactly once before being evaluated or returned, and an
empty normalized value is treated the same as no value at all -- a whitespace-only string
is not a value, it is a non-answer with a string type, and I5 requires abstaining on it
rather than passing it through as if it were a genuine extraction.
"""

from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.config.form_schema import FormFieldSpec
from app.extraction.constants import DEFAULT_RETRIEVAL_TOP_K
from app.extraction.llm_client import LLMProviderError, generate_structured
from app.retrieval.retriever import RetrievedEvidence, retrieve_for_field


class _FieldExtractionResponse(BaseModel):
    value: str | None
    source_chunk_index: int | None
    # DECISIONS.md E7: how directly and unambiguously the cited chunk supports `value`.
    # Bounds enforced here so an out-of-range score fails structured-output parsing inside
    # generate_structured() rather than being silently clamped.
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


@dataclass(frozen=True)
class ExtractionProvenance:
    document_id: str
    page_number: int


@dataclass(frozen=True)
class ExtractionResult:
    """`value`, `provenance`, and `confidence` are all `None` together (abstention) or all
    set -- there is nothing to be confident about when nothing was extracted."""

    value: str | None
    provenance: ExtractionProvenance | None
    confidence: float | None


_ABSTAINED = ExtractionResult(value=None, provenance=None, confidence=None)


def extract_field(
    case_id: str, field: FormFieldSpec, top_k: int = DEFAULT_RETRIEVAL_TOP_K
) -> ExtractionResult:
    evidence = retrieve_for_field(case_id=case_id, field_label=field.label, top_k=top_k)
    if not evidence:
        return _ABSTAINED

    parsed = generate_structured(_build_prompt(field, evidence), _FieldExtractionResponse)

    if parsed.value is None or parsed.source_chunk_index is None:
        return _ABSTAINED

    normalized_value = parsed.value.strip()
    if not normalized_value:
        # Whitespace-only or empty is not a value -- I5/G4: abstain, never invent.
        return _ABSTAINED
    if not 0 <= parsed.source_chunk_index < len(evidence):
        # The model cited a chunk outside what was actually retrieved -- an ungrounded
        # answer cannot be trusted with provenance it doesn't have. Abstain (I5), don't guess.
        return _ABSTAINED
    if parsed.confidence is None:
        # A value without a confidence score breaks the contract the prompt asked for --
        # fail loudly rather than silently defaulting to some assumed number (CLAUDE.md §5).
        raise LLMProviderError("LLM provider returned a value without a confidence score")

    source = evidence[parsed.source_chunk_index]
    return ExtractionResult(
        value=normalized_value,
        provenance=ExtractionProvenance(document_id=source.document_id, page_number=source.page_number),
        confidence=parsed.confidence,
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
        "respond with that value, the index of the chunk that supports it "
        "(source_chunk_index), and a confidence score between 0.0 and 1.0 reflecting how "
        "directly and unambiguously the evidence states this exact value -- 1.0 means the "
        "evidence states it explicitly and unambiguously; lower values reflect ambiguity, "
        "formatting uncertainty, or only partial support. If the evidence does not contain "
        "this field's value, respond with value=null, source_chunk_index=null, and "
        "confidence=null. Never invent a plausible value that is not directly stated in "
        "the evidence."
    )
