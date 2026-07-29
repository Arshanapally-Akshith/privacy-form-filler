"""Conditional edges and bounded retry tests (`BUILD.md` Phase 5, tasks 5+6;
`ARCHITECTURE.md` §7).

Forced-path tests with the LLM stubbed, per `BUILD.md`'s own instruction for this phase --
edges are exercised deterministically, not via real verifier judgment:
  - injected low confidence / re_retrieve -> retry edge taken, retry_count incremented
  - clean, accepted field -> accept edge, no retry
  - escalate -> human-review-shaped state (`FieldState.CONFLICT`), no retry
  - repeated re_retrieve terminates at the budget (`DECISIONS.md` E6), never loops -- the
    exact number of extraction attempts is asserted, not just "it stopped"

Two layers of tests: `apply_verifier_decision` in isolation (a pure state-transition
function -- no LLM/retrieval stubbing needed, since it only reads an already-populated
`VerifierTrace`), and the full compiled graph via `run_graph` (proving the routing table
end to end, with `extract_field`/`retrieve_for_field`/the verifier's boundary call all
stubbed per `CLAUDE.md` §4).
"""

from datetime import date

import pytest

from app.api.models import FieldRecord, FieldState
from app.boundary.mode import PrivacyMode
from app.config.form_schema import FieldType, FormFieldSpec, FormSchema
from app.extraction.constants import DEFAULT_RETRIEVAL_TOP_K
from app.extraction.extractor import ExtractionProvenance, ExtractionResult
from app.orchestration.constants import RETRY_BUDGET, RETRY_RETRIEVAL_TOP_K
from app.orchestration.graph import run_graph
from app.orchestration.nodes import apply_verifier_decision
from app.orchestration.state import (
    FieldGraphState,
    OrchestrationState,
    VerifierDecision,
    VerifierTrace,
    new_orchestration_state,
)
from app.orchestration.verifier import _VerifierResponse
from app.retrieval.retriever import RetrievedEvidence

_REFERENCE_DATE = date(2026, 1, 1)

_SINGLE_FIELD_SCHEMA = FormSchema(
    id="single_field_form",
    name="Single Field Form",
    description="Minimal schema for conditional-edge and retry tests.",
    fields=[
        FormFieldSpec(
            name="date_of_birth", label="Date of Birth", type=FieldType.DATE, required=True, policy_action_ref="dob"
        ),
    ],
)

_STUB_EVIDENCE = [
    RetrievedEvidence(document_id="doc-1", page_number=1, chunk_index=0, text="stub evidence", score=0.5),
]


# --- apply_verifier_decision: pure state-transition unit tests -----------------------


def _state_with_trace(decision: VerifierDecision, retry_count: int) -> OrchestrationState:
    field_state = FieldGraphState(
        record=FieldRecord(name="date_of_birth", label="Date of Birth", value="1990-01-01", state=FieldState.FILLED),
        retry_count=retry_count,
        verifier_traces=[VerifierTrace(field_name="date_of_birth", decision=decision, reasoning="stub", evidence=())],
    )
    return OrchestrationState(
        case_id="case-unit",
        form_schema=_SINGLE_FIELD_SCHEMA,
        privacy_mode=PrivacyMode.NONE,
        reference_date=_REFERENCE_DATE,
        fields={"date_of_birth": field_state},
        current_field_name="date_of_birth",
    )


def test_apply_verifier_decision_accept_leaves_field_state_and_retry_count_unchanged() -> None:
    state = _state_with_trace(VerifierDecision.ACCEPT, retry_count=0)

    updated = apply_verifier_decision(state)

    field_state = updated["fields"]["date_of_birth"]
    assert field_state.record.state == FieldState.FILLED
    assert field_state.retry_count == 0


def test_apply_verifier_decision_escalate_sets_conflict_and_does_not_retry() -> None:
    state = _state_with_trace(VerifierDecision.ESCALATE, retry_count=0)

    updated = apply_verifier_decision(state)

    field_state = updated["fields"]["date_of_birth"]
    assert field_state.record.state == FieldState.CONFLICT
    assert field_state.record.value == "1990-01-01"  # value preserved, only the state changes
    assert field_state.retry_count == 0


def test_apply_verifier_decision_re_retrieve_under_budget_increments_retry_count() -> None:
    state = _state_with_trace(VerifierDecision.RE_RETRIEVE, retry_count=0)
    assert 0 < RETRY_BUDGET  # precondition this test relies on

    updated = apply_verifier_decision(state)

    field_state = updated["fields"]["date_of_birth"]
    assert field_state.retry_count == 1
    assert field_state.record.state == FieldState.FILLED  # not flagged yet -- a retry follows


def test_apply_verifier_decision_re_retrieve_at_budget_sets_low_confidence_and_stops_incrementing() -> None:
    state = _state_with_trace(VerifierDecision.RE_RETRIEVE, retry_count=RETRY_BUDGET)

    updated = apply_verifier_decision(state)

    field_state = updated["fields"]["date_of_birth"]
    assert field_state.retry_count == RETRY_BUDGET  # not incremented past the budget
    assert field_state.record.state == FieldState.LOW_CONFIDENCE


# --- full graph: routing + bounded retry, end to end ----------------------------------


def _stub_pipeline_with_decisions(
    monkeypatch: pytest.MonkeyPatch, decisions: list[VerifierDecision]
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Stubs extraction, the verifier's evidence retrieval, and the verifier's own
    boundary call. `decisions` is consumed one entry per verify call, in order (the last
    entry repeats if verified more times than the list has entries -- not expected to be
    exercised by these tests, since every scenario below supplies exactly as many
    decisions as verification calls are expected)."""
    extraction_calls: list[dict[str, object]] = []
    verify_calls: list[dict[str, object]] = []

    def _fake_extract(
        case_id: str,
        field: FormFieldSpec,
        top_k: int = DEFAULT_RETRIEVAL_TOP_K,
        privacy_mode: PrivacyMode = PrivacyMode.NONE,
        reference_date: date | None = None,
    ) -> ExtractionResult:
        extraction_calls.append({"field_name": field.name, "top_k": top_k})
        return ExtractionResult(
            value="1990-01-01",
            provenance=ExtractionProvenance(document_id="doc-1", page_number=1),
            confidence=0.5,
        )

    def _fake_retrieve(case_id: str, field_label: str, top_k: int) -> list[RetrievedEvidence]:
        return _STUB_EVIDENCE

    def _fake_verify(prompt: str, response_schema: type, **kwargs: object) -> _VerifierResponse:
        idx = min(len(verify_calls), len(decisions) - 1)
        verify_calls.append({"field_name": kwargs["field_name"]})
        return _VerifierResponse(decision=decisions[idx], reasoning="stub reasoning")

    monkeypatch.setattr("app.orchestration.nodes.extract_field", _fake_extract)
    monkeypatch.setattr("app.orchestration.nodes.retrieve_for_field", _fake_retrieve)
    monkeypatch.setattr("app.orchestration.verifier.generate_structured_protected", _fake_verify)
    return extraction_calls, verify_calls


def test_accept_proceeds_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    extraction_calls, verify_calls = _stub_pipeline_with_decisions(monkeypatch, [VerifierDecision.ACCEPT])

    state = new_orchestration_state("case-accept", _SINGLE_FIELD_SCHEMA, PrivacyMode.NONE, _REFERENCE_DATE)
    final = run_graph(state)

    assert len(extraction_calls) == 1
    assert extraction_calls[0]["top_k"] == DEFAULT_RETRIEVAL_TOP_K
    assert len(verify_calls) == 1

    field_state = final.fields["date_of_birth"]
    assert field_state.retry_count == 0
    assert field_state.record.state == FieldState.FILLED
    assert field_state.record.value == "1990-01-01"


def test_retry_follows_the_retry_edge_and_increments_retry_count(monkeypatch: pytest.MonkeyPatch) -> None:
    extraction_calls, verify_calls = _stub_pipeline_with_decisions(
        monkeypatch, [VerifierDecision.RE_RETRIEVE, VerifierDecision.ACCEPT]
    )

    state = new_orchestration_state("case-retry", _SINGLE_FIELD_SCHEMA, PrivacyMode.NONE, _REFERENCE_DATE)
    final = run_graph(state)

    assert len(extraction_calls) == 2
    assert extraction_calls[0]["top_k"] == DEFAULT_RETRIEVAL_TOP_K
    assert extraction_calls[1]["top_k"] == RETRY_RETRIEVAL_TOP_K  # E18, only on the retry path
    assert len(verify_calls) == 2

    field_state = final.fields["date_of_birth"]
    assert field_state.retry_count == 1
    assert field_state.record.state == FieldState.FILLED  # accepted on the retry attempt
    assert [t.decision for t in field_state.verifier_traces] == [
        VerifierDecision.RE_RETRIEVE,
        VerifierDecision.ACCEPT,
    ]


def test_escalate_marks_conflict_state_without_retrying(monkeypatch: pytest.MonkeyPatch) -> None:
    extraction_calls, verify_calls = _stub_pipeline_with_decisions(monkeypatch, [VerifierDecision.ESCALATE])

    state = new_orchestration_state("case-escalate", _SINGLE_FIELD_SCHEMA, PrivacyMode.NONE, _REFERENCE_DATE)
    final = run_graph(state)

    assert len(extraction_calls) == 1  # escalate never retries
    assert len(verify_calls) == 1

    field_state = final.fields["date_of_birth"]
    assert field_state.retry_count == 0
    assert field_state.record.state == FieldState.CONFLICT
    assert field_state.record.value == "1990-01-01"  # value preserved, just flagged


def test_retries_terminate_after_exactly_budget_plus_one_extraction_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An adversarial verifier that always rejects must still terminate -- concrete proof
    there is no possibility of an unbounded loop (`DECISIONS.md` E6), not just an
    assumption that it "eventually stops." The number of decisions supplied deliberately
    exceeds what should ever be consumed."""
    always_re_retrieve = [VerifierDecision.RE_RETRIEVE] * (RETRY_BUDGET + 5)
    extraction_calls, verify_calls = _stub_pipeline_with_decisions(monkeypatch, always_re_retrieve)

    state = new_orchestration_state("case-exhausted", _SINGLE_FIELD_SCHEMA, PrivacyMode.NONE, _REFERENCE_DATE)
    final = run_graph(state)

    assert len(extraction_calls) == RETRY_BUDGET + 1
    assert len(verify_calls) == RETRY_BUDGET + 1

    field_state = final.fields["date_of_birth"]
    assert field_state.retry_count == RETRY_BUDGET
    assert field_state.record.state == FieldState.LOW_CONFIDENCE
    assert all(t.decision == VerifierDecision.RE_RETRIEVE for t in field_state.verifier_traces)
