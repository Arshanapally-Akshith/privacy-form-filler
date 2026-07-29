"""Verifier (`ARCHITECTURE.md` §4.1, §7.1; `BUILD.md` Phase 5, tasks 3+4).

The one genuinely agentic component in the system (`ARCH §4.1`): an LLM judge that
examines a candidate field value alongside the *full* retrieved evidence set -- not just
whichever chunk extraction happened to cite -- and decides accept / re-retrieve /
escalate, with its reasoning (`ARCH §7.1`, `DECISIONS.md` R6). Seeing every retrieved
chunk, not only the cited one, is what makes noticing disagreement between documents
possible at all; a verifier shown only the extractor's chosen chunk could never detect a
conflict the extractor itself didn't surface.

Routes through the same single call site Invariant I3 requires
(`app.boundary.llm.generate_structured_protected`) -- there is no separate LLM call path
for the verifier. Structured output only (`_VerifierResponse`), the same pattern
`app.extraction.extractor._FieldExtractionResponse` already establishes: no free-form
parsing of the model's reasoning.

This module returns a `VerifierTrace` and nothing else -- it makes no decision about
graph routing, retry bookkeeping, or where the trace gets stored. That is
`app.orchestration.nodes.verify_current_field`'s job, not this one's; `verify_field` below
takes plain scalar/evidence arguments, not an `OrchestrationState`, so it stays as
decoupled from the graph as `app.extraction.extractor.extract_field` already is.
"""

from datetime import date

from pydantic import BaseModel, Field

from app.boundary.capture import PayloadCapture
from app.boundary.llm import generate_structured_protected
from app.boundary.mode import PrivacyMode
from app.config.form_schema import FormFieldSpec
from app.orchestration.state import VerifierDecision, VerifierTrace
from app.retrieval.retriever import RetrievedEvidence


class _VerifierResponse(BaseModel):
    decision: VerifierDecision
    reasoning: str = Field(min_length=1)


def verify_field(
    case_id: str,
    field: FormFieldSpec,
    candidate_value: str | None,
    candidate_confidence: float | None,
    evidence: list[RetrievedEvidence],
    privacy_mode: PrivacyMode,
    reference_date: date | None = None,
    capture: PayloadCapture | None = None,
) -> VerifierTrace:
    """Same call-site contract as `extract_field`: `field.name` / `field.policy_action_ref`
    / `reference_date` are forwarded to the boundary unconditionally regardless of
    `privacy_mode` (this module has no privacy_mode-specific branching of its own -- that
    decision lives entirely in `app.boundary`), and `capture` is forwarded unconditionally
    for the same reason `extract_field` accepts it: the outbound-PII-assertion harness
    (Invariant P2) must be able to see the verifier's outbound payload too, not only
    extraction's -- "no raw PII value appears in any payload sent to an external LLM"
    covers every call through this boundary, not just the extraction one.
    """
    parsed = generate_structured_protected(
        _build_prompt(field, candidate_value, candidate_confidence, evidence),
        _VerifierResponse,
        session_id=case_id,
        privacy_mode=privacy_mode,
        field_name=field.name,
        policy_action_ref=field.policy_action_ref,
        reference_date=reference_date,
        capture=capture,
    )
    return VerifierTrace(
        field_name=field.name,
        # VerifierDecision is a str Enum; app.boundary.llm._reverse_response_strings treats
        # every string-valued response field generically and may hand back a plain `str`
        # after a no-op reversal (none of "accept"/"re_retrieve"/"escalate" ever match a
        # recorded token, so the value itself is untouched -- only its Python type could
        # change). Re-wrapping here guarantees `VerifierTrace.decision` is always a real
        # `VerifierDecision` member regardless of which privacy_mode ran.
        decision=VerifierDecision(parsed.decision),
        reasoning=parsed.reasoning,
        evidence=tuple(evidence),
    )


def _build_prompt(
    field: FormFieldSpec,
    candidate_value: str | None,
    candidate_confidence: float | None,
    evidence: list[RetrievedEvidence],
) -> str:
    evidence_block = "\n\n".join(
        f"[chunk {i}] (document={item.document_id}, page={item.page_number})\n{item.text}"
        for i, item in enumerate(evidence)
    )
    candidate_block = (
        f"Candidate value: {candidate_value!r} (extractor confidence: {candidate_confidence})"
        if candidate_value is not None
        else "Candidate value: none -- the extractor found no supporting evidence and abstained."
    )
    return (
        "You are verifying a single form field's extracted value against the full set of "
        "evidence excerpts retrieved for it from a user's uploaded documents. Decide whether "
        "the candidate value is correct and well-supported by this evidence.\n\n"
        f"Field: {field.label}.\n\n"
        f"{candidate_block}\n\n"
        "Evidence (every chunk retrieved for this field, not just whichever one the "
        f"extractor cited):\n{evidence_block if evidence_block else '(no evidence was retrieved)'}\n\n"
        "Respond with exactly one decision:\n"
        "- accept: the candidate value is directly and unambiguously supported by the "
        "evidence, or the evidence genuinely contains no value and abstaining was correct.\n"
        "- re_retrieve: the evidence may contain the answer but is inconclusive or "
        "ambiguous as given, and reviewing more evidence could resolve it.\n"
        "- escalate: the evidence directly conflicts -- for example, two chunks state "
        "different values for the same field -- and a human should decide, not a retry.\n"
        "Always include your reasoning for the decision."
    )
