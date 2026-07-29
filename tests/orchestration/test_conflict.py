"""Conflict-detection forced-path test (`BUILD.md` Phase 5, task 7; `ARCHITECTURE.md` §7).

BUILD.md's named scenario: conflicting values across two documents for the same field ->
human-review edge taken. Routing mechanics (escalate -> `FieldState.CONFLICT`, no retry)
already exist and are generically tested as of commit 6
(`tests/orchestration/test_edges.py::test_escalate_marks_conflict_state_without_retrying`);
what this test adds is the specific scenario, exercised through the *real* retrieval stack
rather than a hand-fabricated evidence list -- two chunks from two different documents,
each stating a different date of birth, seeded into a real case index so
`retrieve_for_field` (unstubbed) genuinely retrieves both into one evidence set. Only the
two LLM calls (extraction's candidate value, the verifier's decision) are stubbed, and the
verifier's response is a fixed, deterministic `escalate` -- this test is about the graph's
routing mechanism given that decision, not about whether a real verifier would reach it.
"""

from datetime import date

import pytest

from app.api.models import FieldState
from app.boundary.mode import PrivacyMode
from app.config.form_schema import FieldType, FormFieldSpec, FormSchema
from app.extraction.extractor import _FieldExtractionResponse
from app.ingest.chunker import Chunk
from app.orchestration.graph import run_graph
from app.orchestration.state import VerifierDecision, new_orchestration_state
from app.orchestration.verifier import _VerifierResponse
from app.retrieval.store import case_index_registry, embed_chunks

_REFERENCE_DATE = date(2026, 1, 1)

_SCHEMA = FormSchema(
    id="conflict_test_form",
    name="Conflict Test Form",
    description="Single-field schema for the conflicting-DOB forced-path test.",
    fields=[
        FormFieldSpec(
            name="date_of_birth", label="Date of Birth", type=FieldType.DATE, required=True, policy_action_ref="dob"
        ),
    ],
)

_PASSPORT_DOB = "1990-01-01"
_AADHAAR_DOB = "1991-05-05"


def _uniform_vector(texts: list[str]) -> list[list[float]]:
    return [[1.0, 0.0] for _ in texts]


def _seed_conflicting_documents(monkeypatch: pytest.MonkeyPatch, case_id: str) -> None:
    """Two documents, two different dates of birth for the same person -- the exact shape
    BUILD.md names ("conflicting values across documents"). Both chunks get identical
    embedding vectors, so `retrieve_for_field`'s real similarity search returns both in the
    same evidence set regardless of tie-breaking order."""
    monkeypatch.setattr("app.retrieval.store.embed_texts", _uniform_vector)
    chunks = [
        Chunk(document_id="passport.pdf", page_number=1, chunk_index=0, text=f"Date of Birth: {_PASSPORT_DOB}"),
        Chunk(document_id="aadhaar.pdf", page_number=2, chunk_index=0, text=f"Date of Birth: {_AADHAAR_DOB}"),
    ]
    case_index_registry.get_or_create(case_id).add(embed_chunks(chunks))
    monkeypatch.setattr("app.retrieval.retriever.embed_texts", _uniform_vector)


def test_conflicting_dob_across_two_documents_routes_to_conflict_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_id = "case-conflicting-dob"
    _seed_conflicting_documents(monkeypatch, case_id)

    extraction_calls: list[dict[str, object]] = []
    verify_prompts: list[str] = []

    def _fake_extract(prompt: str, response_schema: type, **kwargs: object) -> _FieldExtractionResponse:
        extraction_calls.append({"prompt": prompt})
        # Either conflicting chunk is a valid citation -- index 0 is always in range
        # regardless of which chunk retrieval happened to place first. Which one the
        # extractor picks is not what this test is about (see module docstring): the
        # verifier is shown the *full* evidence set either way, not just this citation.
        return _FieldExtractionResponse(value=_PASSPORT_DOB, source_chunk_index=0, confidence=0.7)

    def _fake_verify(prompt: str, response_schema: type, **kwargs: object) -> _VerifierResponse:
        verify_prompts.append(prompt)
        return _VerifierResponse(
            decision=VerifierDecision.ESCALATE,
            reasoning="Two documents state different dates of birth for the same field.",
        )

    monkeypatch.setattr("app.extraction.extractor.generate_structured_protected", _fake_extract)
    monkeypatch.setattr("app.orchestration.verifier.generate_structured_protected", _fake_verify)

    state = new_orchestration_state(case_id, _SCHEMA, PrivacyMode.NONE, _REFERENCE_DATE)
    final = run_graph(state)

    # Evidence plumbing: the verifier actually saw both conflicting values, not just the
    # one the extractor cited.
    assert len(verify_prompts) == 1
    assert _PASSPORT_DOB in verify_prompts[0]
    assert _AADHAAR_DOB in verify_prompts[0]

    field_state = final.fields["date_of_birth"]

    # FieldState.CONFLICT is the graph's human-review routing signal (ARCH §7's "verifier
    # detects conflicting values across documents -> human review" row): escalate never
    # retries, so this must be the terminal state, reached on the first and only attempt.
    assert field_state.record.state == FieldState.CONFLICT
    assert field_state.record.value == _PASSPORT_DOB  # candidate value preserved, just flagged
    assert field_state.retry_count == 0
    assert len(extraction_calls) == 1  # no retry taken

    assert len(field_state.verifier_traces) == 1
    trace = field_state.verifier_traces[0]
    assert trace.decision is VerifierDecision.ESCALATE
    assert trace.field_name == "date_of_birth"

    # Provenance for the conflicting evidence is preserved on the persisted trace -- both
    # documents, with their correct page numbers, not just one.
    assert len(trace.evidence) == 2
    evidence_by_document = {item.document_id: item for item in trace.evidence}
    assert set(evidence_by_document) == {"passport.pdf", "aadhaar.pdf"}
    assert evidence_by_document["passport.pdf"].page_number == 1
    assert _PASSPORT_DOB in evidence_by_document["passport.pdf"].text
    assert evidence_by_document["aadhaar.pdf"].page_number == 2
    assert _AADHAAR_DOB in evidence_by_document["aadhaar.pdf"].text
