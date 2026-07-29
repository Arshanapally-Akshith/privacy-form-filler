"""Verifier tests (`BUILD.md` Phase 5, tasks 3+4; `ARCHITECTURE.md` §7.1).

`generate_structured_protected` is stubbed throughout (`CLAUDE.md` §4: no live LLM in
tests) with three canned responses -- accept, re_retrieve, escalate -- proving `verify_field`
produces a correctly-shaped `VerifierTrace` for each, that it routes through the single
boundary call site rather than any other path, and that the full retrieved evidence set
(not just one chunk) reaches both the prompt and the persisted trace.
"""

from datetime import date

import pytest

from app.boundary.mode import PrivacyMode
from app.config.form_schema import FieldType, FormFieldSpec
from app.orchestration.state import VerifierDecision
from app.orchestration.verifier import _VerifierResponse, verify_field
from app.retrieval.retriever import RetrievedEvidence

_REFERENCE_DATE = date(2026, 1, 1)

_FIELD = FormFieldSpec(
    name="date_of_birth",
    label="Date of Birth",
    type=FieldType.DATE,
    required=True,
    policy_action_ref="dob",
)

_EVIDENCE = [
    RetrievedEvidence(document_id="doc-1", page_number=1, chunk_index=0, text="DOB: 1990-01-01", score=0.9),
    RetrievedEvidence(document_id="doc-2", page_number=3, chunk_index=2, text="DOB: 1991-05-05", score=0.85),
]


def _stub_returning(
    monkeypatch: pytest.MonkeyPatch, decision: VerifierDecision, reasoning: str
) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def _fake(prompt: str, response_schema: type, **kwargs: object) -> _VerifierResponse:
        calls.append({"prompt": prompt, "response_schema": response_schema, **kwargs})
        return _VerifierResponse(decision=decision, reasoning=reasoning)

    monkeypatch.setattr("app.orchestration.verifier.generate_structured_protected", _fake)
    return calls


def test_accept_decision_produces_a_matching_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_returning(monkeypatch, VerifierDecision.ACCEPT, "Evidence directly states the value.")

    trace = verify_field(
        case_id="case-1",
        field=_FIELD,
        candidate_value="1990-01-01",
        candidate_confidence=0.95,
        evidence=_EVIDENCE,
        privacy_mode=PrivacyMode.NONE,
        reference_date=_REFERENCE_DATE,
    )

    assert trace.field_name == "date_of_birth"
    assert trace.decision is VerifierDecision.ACCEPT
    assert trace.reasoning == "Evidence directly states the value."
    assert len(calls) == 1


def test_re_retrieve_decision_produces_a_matching_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_returning(monkeypatch, VerifierDecision.RE_RETRIEVE, "Ambiguous -- worth another look.")

    trace = verify_field(
        case_id="case-2",
        field=_FIELD,
        candidate_value="1990-01-01",
        candidate_confidence=0.4,
        evidence=_EVIDENCE,
        privacy_mode=PrivacyMode.NONE,
        reference_date=_REFERENCE_DATE,
    )

    assert trace.decision is VerifierDecision.RE_RETRIEVE
    assert trace.reasoning == "Ambiguous -- worth another look."


def test_escalate_decision_produces_a_matching_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_returning(monkeypatch, VerifierDecision.ESCALATE, "Two documents disagree on date of birth.")

    trace = verify_field(
        case_id="case-3",
        field=_FIELD,
        candidate_value="1990-01-01",
        candidate_confidence=0.8,
        evidence=_EVIDENCE,
        privacy_mode=PrivacyMode.NONE,
        reference_date=_REFERENCE_DATE,
    )

    assert trace.decision is VerifierDecision.ESCALATE
    assert trace.reasoning == "Two documents disagree on date of birth."


def test_trace_carries_the_full_evidence_set_not_just_one_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_returning(monkeypatch, VerifierDecision.ESCALATE, "conflict")

    trace = verify_field(
        case_id="case-4",
        field=_FIELD,
        candidate_value="1990-01-01",
        candidate_confidence=0.8,
        evidence=_EVIDENCE,
        privacy_mode=PrivacyMode.NONE,
        reference_date=_REFERENCE_DATE,
    )

    assert trace.evidence == tuple(_EVIDENCE)
    assert len(trace.evidence) == 2
    assert {item.document_id for item in trace.evidence} == {"doc-1", "doc-2"}


def test_prompt_includes_every_retrieved_chunk_not_just_the_candidates_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_returning(monkeypatch, VerifierDecision.ESCALATE, "conflict")

    verify_field(
        case_id="case-5",
        field=_FIELD,
        candidate_value="1990-01-01",
        candidate_confidence=0.8,
        evidence=_EVIDENCE,
        privacy_mode=PrivacyMode.NONE,
        reference_date=_REFERENCE_DATE,
    )

    prompt = calls[0]["prompt"]
    assert isinstance(prompt, str)
    assert "DOB: 1990-01-01" in prompt
    assert "DOB: 1991-05-05" in prompt
    assert "doc-1" in prompt
    assert "doc-2" in prompt


def test_uses_the_structured_response_schema_not_free_form_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_returning(monkeypatch, VerifierDecision.ACCEPT, "fine")

    verify_field(
        case_id="case-6",
        field=_FIELD,
        candidate_value="1990-01-01",
        candidate_confidence=0.9,
        evidence=_EVIDENCE,
        privacy_mode=PrivacyMode.NONE,
        reference_date=_REFERENCE_DATE,
    )

    assert calls[0]["response_schema"] is _VerifierResponse


def test_routes_through_the_single_boundary_call_site_with_the_expected_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_returning(monkeypatch, VerifierDecision.ACCEPT, "fine")

    verify_field(
        case_id="case-7",
        field=_FIELD,
        candidate_value="1990-01-01",
        candidate_confidence=0.9,
        evidence=_EVIDENCE,
        privacy_mode=PrivacyMode.POLICY_ENGINE,
        reference_date=_REFERENCE_DATE,
    )

    call = calls[0]
    assert call["session_id"] == "case-7"
    assert call["privacy_mode"] is PrivacyMode.POLICY_ENGINE
    assert call["field_name"] == "date_of_birth"
    assert call["policy_action_ref"] == "dob"
    assert call["reference_date"] == _REFERENCE_DATE


def test_decision_is_normalized_to_a_real_enum_even_if_the_boundary_returns_a_plain_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`app.boundary.llm._reverse_response_strings` treats every string-valued response
    field generically and rebuilds the model via `model_copy(update=...)`, which does not
    re-validate -- under full_tokenize/policy_engine this could hand back `decision` as a
    plain `str` rather than a `VerifierDecision` member (the value is unchanged, since none
    of the three decision strings ever match a recorded token, but the Python type could
    still flatten). `verify_field` must not propagate that as a bare string."""

    def _fake(prompt: str, response_schema: type, **kwargs: object) -> _VerifierResponse:
        response = _VerifierResponse(decision=VerifierDecision.ACCEPT, reasoning="fine")
        return response.model_copy(update={"decision": "accept"})

    monkeypatch.setattr("app.orchestration.verifier.generate_structured_protected", _fake)

    trace = verify_field(
        case_id="case-8",
        field=_FIELD,
        candidate_value="1990-01-01",
        candidate_confidence=0.9,
        evidence=_EVIDENCE,
        privacy_mode=PrivacyMode.NONE,
        reference_date=_REFERENCE_DATE,
    )

    assert trace.decision is VerifierDecision.ACCEPT
    assert isinstance(trace.decision, VerifierDecision)
