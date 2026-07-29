"""Graph state schema tests (`BUILD.md` Phase 5, task 1; `ARCHITECTURE.md` §7).

No graph, no LLM, no retrieval here -- this module only proves the state shape itself:
what a fresh case's state looks like, and that the mutable-default-factory fields
(`fields`, `pending_field_names`, `verifier_traces`) are genuinely per-instance rather than
accidentally shared, which is the classic dataclass bug this kind of state is most exposed
to.
"""

import dataclasses
from datetime import date

import pytest

from app.api.models import FieldRecord, FieldState
from app.boundary.mode import PrivacyMode
from app.config.form_schema import FieldType, FormFieldSpec, FormSchema
from app.orchestration.state import (
    FieldGraphState,
    OrchestrationState,
    VerifierDecision,
    VerifierTrace,
    new_orchestration_state,
)
from app.retrieval.retriever import RetrievedEvidence

_REFERENCE_DATE = date(2026, 1, 1)

_SCHEMA = FormSchema(
    id="test_form",
    name="Test Form",
    description="A form for state tests.",
    fields=[
        FormFieldSpec(
            name="full_name",
            label="Full Name",
            type=FieldType.NAME,
            required=True,
            policy_action_ref=None,
        ),
        FormFieldSpec(
            name="date_of_birth",
            label="Date of Birth",
            type=FieldType.DATE,
            required=True,
            policy_action_ref="dob",
        ),
        FormFieldSpec(
            name="pin_code",
            label="PIN Code",
            type=FieldType.LOCATION,
            required=False,
            policy_action_ref="pin_code",
        ),
    ],
)


def test_new_state_seeds_one_missing_field_per_schema_field_in_declared_order() -> None:
    state = new_orchestration_state(
        case_id="case-1",
        form_schema=_SCHEMA,
        privacy_mode=PrivacyMode.NONE,
        reference_date=_REFERENCE_DATE,
    )

    assert state.case_id == "case-1"
    assert state.form_schema is _SCHEMA
    assert state.privacy_mode is PrivacyMode.NONE
    assert state.reference_date == _REFERENCE_DATE
    assert state.pending_field_names == ["full_name", "date_of_birth", "pin_code"]
    assert set(state.fields) == {"full_name", "date_of_birth", "pin_code"}

    for spec in _SCHEMA.fields:
        field_state = state.fields[spec.name]
        assert field_state.record.name == spec.name
        assert field_state.record.label == spec.label
        assert field_state.record.state == FieldState.MISSING
        assert field_state.record.value is None
        assert field_state.record.confidence is None
        assert field_state.record.provenance is None
        assert field_state.retry_count == 0
        assert field_state.verifier_traces == []


def test_orchestration_state_default_factories_are_not_shared_across_instances() -> None:
    first = new_orchestration_state("case-a", _SCHEMA, PrivacyMode.NONE, _REFERENCE_DATE)
    second = new_orchestration_state("case-b", _SCHEMA, PrivacyMode.NONE, _REFERENCE_DATE)

    first.pending_field_names.pop()
    first.fields["full_name"].retry_count += 1
    first.fields["full_name"].verifier_traces.append(
        VerifierTrace(
            field_name="full_name",
            decision=VerifierDecision.ACCEPT,
            reasoning="Evidence directly states the name.",
            evidence=(),
        )
    )

    assert len(second.pending_field_names) == 3
    assert second.fields["full_name"].retry_count == 0
    assert second.fields["full_name"].verifier_traces == []


def test_field_graph_state_defaults_are_independent_per_instance() -> None:
    a = FieldGraphState(record=FieldRecord(name="x", label="X", state=FieldState.MISSING))
    b = FieldGraphState(record=FieldRecord(name="y", label="Y", state=FieldState.MISSING))

    a.verifier_traces.append(
        VerifierTrace(
            field_name="x", decision=VerifierDecision.ESCALATE, reasoning="conflict", evidence=()
        )
    )

    assert a.verifier_traces != b.verifier_traces
    assert b.verifier_traces == []


def test_verifier_trace_is_immutable() -> None:
    trace = VerifierTrace(
        field_name="date_of_birth",
        decision=VerifierDecision.RE_RETRIEVE,
        reasoning="Confidence too low to accept.",
        evidence=(),
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        trace.reasoning = "changed"  # type: ignore[misc]


def test_verifier_trace_carries_the_full_retrieved_evidence_set_not_just_one_chunk() -> None:
    evidence = (
        RetrievedEvidence(
            document_id="doc-1", page_number=1, chunk_index=0, text="DOB: 1990-01-01", score=0.9
        ),
        RetrievedEvidence(
            document_id="doc-2", page_number=3, chunk_index=2, text="DOB: 1991-05-05", score=0.85
        ),
    )

    trace = VerifierTrace(
        field_name="date_of_birth",
        decision=VerifierDecision.ESCALATE,
        reasoning="Two documents disagree on date of birth.",
        evidence=evidence,
    )

    assert len(trace.evidence) == 2
    assert {item.document_id for item in trace.evidence} == {"doc-1", "doc-2"}


def test_orchestration_state_is_constructible_directly_with_empty_defaults() -> None:
    state = OrchestrationState(
        case_id="case-direct",
        form_schema=_SCHEMA,
        privacy_mode=PrivacyMode.POLICY_ENGINE,
        reference_date=_REFERENCE_DATE,
    )

    assert state.fields == {}
    assert state.pending_field_names == []
