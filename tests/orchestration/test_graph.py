"""Standalone graph tests (`BUILD.md` Phase 5, tasks 2-4).

Proves the graph reproduces `app.api.cases._process_case`'s existing extraction behavior
(same fields, same order, same arguments) and, since commit 5, that verification runs
immediately after extraction for the same field, persists exactly one `VerifierTrace` per
field, and does not yet affect routing -- every field is processed exactly once regardless
of the verifier's decision (structurally linear; commit 6 is what makes the decision
matter). `extract_field`, `retrieve_for_field`, and the verifier's own boundary call are
all stubbed (`CLAUDE.md` §4: no live LLM or retrieval calls in tests).
"""

from datetime import date

import pytest

from app.api.models import FieldState
from app.boundary.mode import PrivacyMode
from app.config.form_schema import FieldType, FormFieldSpec, FormSchema
from app.extraction.constants import DEFAULT_RETRIEVAL_TOP_K
from app.extraction.extractor import ExtractionProvenance, ExtractionResult
from app.orchestration.graph import run_graph
from app.orchestration.state import VerifierDecision, new_orchestration_state
from app.orchestration.verifier import _VerifierResponse
from app.retrieval.retriever import RetrievedEvidence

_REFERENCE_DATE = date(2026, 1, 1)

_SCHEMA = FormSchema(
    id="test_form",
    name="Test Form",
    description="A form for graph tests.",
    fields=[
        FormFieldSpec(
            name="full_name", label="Full Name", type=FieldType.NAME, required=True, policy_action_ref=None
        ),
        FormFieldSpec(
            name="date_of_birth", label="Date of Birth", type=FieldType.DATE, required=True, policy_action_ref="dob"
        ),
        FormFieldSpec(
            name="pin_code", label="PIN Code", type=FieldType.LOCATION, required=False, policy_action_ref="pin_code"
        ),
    ],
)

_ABSTAINED = ExtractionResult(value=None, provenance=None, confidence=None)

_STUB_EVIDENCE = [
    RetrievedEvidence(document_id="doc-1", page_number=1, chunk_index=0, text="stub evidence", score=0.5),
]


def _stub_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    extraction_responses: dict[str, ExtractionResult],
    verifier_decision: VerifierDecision = VerifierDecision.ACCEPT,
) -> list[dict[str, object]]:
    """Stubs the three points where this graph reaches outside itself: extraction
    (`app.orchestration.nodes.extract_field`), the verifier's evidence retrieval
    (`app.orchestration.nodes.retrieve_for_field`), and the verifier's own boundary call
    (`app.orchestration.verifier.generate_structured_protected`). Returns the recorded
    extraction calls, in order -- the same proof commits 3/4's tests already relied on."""
    extraction_calls: list[dict[str, object]] = []

    def _fake_extract(
        case_id: str,
        field: FormFieldSpec,
        top_k: int = DEFAULT_RETRIEVAL_TOP_K,
        privacy_mode: PrivacyMode = PrivacyMode.NONE,
        reference_date: date | None = None,
    ) -> ExtractionResult:
        extraction_calls.append(
            {
                "case_id": case_id,
                "field_name": field.name,
                "top_k": top_k,
                "privacy_mode": privacy_mode,
                "reference_date": reference_date,
            }
        )
        return extraction_responses[field.name]

    def _fake_retrieve(case_id: str, field_label: str, top_k: int) -> list[RetrievedEvidence]:
        return _STUB_EVIDENCE

    def _fake_verify(prompt: str, response_schema: type, **kwargs: object) -> _VerifierResponse:
        return _VerifierResponse(decision=verifier_decision, reasoning="stub reasoning")

    monkeypatch.setattr("app.orchestration.nodes.extract_field", _fake_extract)
    monkeypatch.setattr("app.orchestration.nodes.retrieve_for_field", _fake_retrieve)
    monkeypatch.setattr("app.orchestration.verifier.generate_structured_protected", _fake_verify)
    return extraction_calls


def test_graph_calls_extract_field_once_per_schema_field_in_declared_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {
        "full_name": _ABSTAINED,
        "date_of_birth": _ABSTAINED,
        "pin_code": _ABSTAINED,
    }
    calls = _stub_pipeline(monkeypatch, responses)

    state = new_orchestration_state("case-1", _SCHEMA, PrivacyMode.FULL_TOKENIZE, _REFERENCE_DATE)
    run_graph(state)

    assert [call["field_name"] for call in calls] == ["full_name", "date_of_birth", "pin_code"]
    for call in calls:
        assert call["case_id"] == "case-1"
        assert call["top_k"] == DEFAULT_RETRIEVAL_TOP_K
        assert call["privacy_mode"] is PrivacyMode.FULL_TOKENIZE
        assert call["reference_date"] == _REFERENCE_DATE


def test_graph_produces_the_same_field_records_a_manual_loop_over_extract_field_would(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {
        "full_name": ExtractionResult(
            value="Asha Rao",
            provenance=ExtractionProvenance(document_id="doc-1", page_number=1),
            confidence=0.95,
        ),
        "date_of_birth": _ABSTAINED,
        "pin_code": ExtractionResult(
            value="560001",
            provenance=ExtractionProvenance(document_id="doc-2", page_number=2),
            confidence=0.8,
        ),
    }
    _stub_pipeline(monkeypatch, responses)

    state = new_orchestration_state("case-2", _SCHEMA, PrivacyMode.NONE, _REFERENCE_DATE)
    final = run_graph(state)

    name_record = final.fields["full_name"].record
    assert name_record.state == FieldState.FILLED
    assert name_record.value == "Asha Rao"
    assert name_record.confidence == 0.95
    assert name_record.provenance is not None
    assert name_record.provenance.document_id == "doc-1"
    assert name_record.provenance.page_number == 1

    dob_record = final.fields["date_of_birth"].record
    assert dob_record.state == FieldState.MISSING
    assert dob_record.value is None
    assert dob_record.confidence is None
    assert dob_record.provenance is None

    pin_record = final.fields["pin_code"].record
    assert pin_record.state == FieldState.FILLED
    assert pin_record.value == "560001"


def test_graph_leaves_no_pending_fields_when_every_field_has_been_processed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {"full_name": _ABSTAINED, "date_of_birth": _ABSTAINED, "pin_code": _ABSTAINED}
    _stub_pipeline(monkeypatch, responses)

    state = new_orchestration_state("case-3", _SCHEMA, PrivacyMode.NONE, _REFERENCE_DATE)
    final = run_graph(state)

    assert final.pending_field_names == []
    assert set(final.fields) == {"full_name", "date_of_birth", "pin_code"}
    assert final.current_field_name == "pin_code"  # the last field processed


def test_graph_persists_one_verifier_trace_per_field_and_never_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {
        "full_name": ExtractionResult(
            value="Asha Rao", provenance=ExtractionProvenance(document_id="doc-1", page_number=1), confidence=0.95
        ),
        "date_of_birth": _ABSTAINED,
        "pin_code": _ABSTAINED,
    }
    _stub_pipeline(monkeypatch, responses, verifier_decision=VerifierDecision.ACCEPT)

    state = new_orchestration_state("case-4", _SCHEMA, PrivacyMode.NONE, _REFERENCE_DATE)
    final = run_graph(state)

    for field_state in final.fields.values():
        assert field_state.retry_count == 0
        assert len(field_state.verifier_traces) == 1
        assert field_state.verifier_traces[0].decision is VerifierDecision.ACCEPT


def test_graph_preserves_case_level_fields_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {"full_name": _ABSTAINED, "date_of_birth": _ABSTAINED, "pin_code": _ABSTAINED}
    _stub_pipeline(monkeypatch, responses)

    state = new_orchestration_state("case-5", _SCHEMA, PrivacyMode.POLICY_ENGINE, _REFERENCE_DATE)
    final = run_graph(state)

    assert final.case_id == "case-5"
    assert final.form_schema.id == _SCHEMA.id
    assert final.privacy_mode is PrivacyMode.POLICY_ENGINE
    assert final.reference_date == _REFERENCE_DATE


@pytest.mark.parametrize(
    "decision", [VerifierDecision.ACCEPT, VerifierDecision.RE_RETRIEVE, VerifierDecision.ESCALATE]
)
def test_graph_processes_every_field_regardless_of_verifier_decision(
    monkeypatch: pytest.MonkeyPatch, decision: VerifierDecision
) -> None:
    """Structurally linear as of this commit (`BUILD.md` Phase 5 commit 5): the verifier's
    decision is computed and persisted, but has no effect on routing yet -- every field is
    processed exactly once regardless of what the verifier decided. Commit 6 is what makes
    the decision matter."""
    responses = {"full_name": _ABSTAINED, "date_of_birth": _ABSTAINED, "pin_code": _ABSTAINED}
    calls = _stub_pipeline(monkeypatch, responses, verifier_decision=decision)

    state = new_orchestration_state("case-linear", _SCHEMA, PrivacyMode.NONE, _REFERENCE_DATE)
    final = run_graph(state)

    assert [call["field_name"] for call in calls] == ["full_name", "date_of_birth", "pin_code"]
    assert final.pending_field_names == []
    for field_state in final.fields.values():
        assert field_state.retry_count == 0
        assert len(field_state.verifier_traces) == 1
        assert field_state.verifier_traces[0].decision is decision


def test_verification_evidence_reaches_the_persisted_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {"full_name": _ABSTAINED, "date_of_birth": _ABSTAINED, "pin_code": _ABSTAINED}
    _stub_pipeline(monkeypatch, responses, verifier_decision=VerifierDecision.ACCEPT)

    state = new_orchestration_state("case-evidence", _SCHEMA, PrivacyMode.NONE, _REFERENCE_DATE)
    final = run_graph(state)

    trace = final.fields["full_name"].verifier_traces[0]
    assert trace.evidence == tuple(_STUB_EVIDENCE)
