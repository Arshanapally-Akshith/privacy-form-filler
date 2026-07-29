"""Standalone graph tests (`BUILD.md` Phase 5, task 2).

Proves the graph reproduces `app.api.cases._process_case`'s existing loop behavior --
same fields, same order, same arguments to `extract_field`, same resulting `FieldRecord`
shapes -- with `extract_field` stubbed (`CLAUDE.md` §4: no live LLM or retrieval calls in
tests). The graph is not wired into the API in this commit; these tests exercise
`app.orchestration.graph` directly.
"""

from datetime import date

import pytest

from app.api.models import FieldState
from app.boundary.mode import PrivacyMode
from app.config.form_schema import FieldType, FormFieldSpec, FormSchema
from app.extraction.constants import DEFAULT_RETRIEVAL_TOP_K
from app.extraction.extractor import ExtractionProvenance, ExtractionResult
from app.orchestration.graph import run_graph
from app.orchestration.state import new_orchestration_state

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


def _stub_extract_field(
    monkeypatch: pytest.MonkeyPatch, responses: dict[str, ExtractionResult]
) -> list[dict[str, object]]:
    """Monkeypatch the exact symbol `app.orchestration.nodes.extract_next_field` calls
    (`extract_field`, imported into that module), and record every call's arguments in
    order -- the same symbol `app.api.cases._process_case` calls today, so this proves the
    graph reaches the extraction node the same way the existing loop does."""
    calls: list[dict[str, object]] = []

    def _fake(
        case_id: str,
        field: FormFieldSpec,
        top_k: int = DEFAULT_RETRIEVAL_TOP_K,
        privacy_mode: PrivacyMode = PrivacyMode.NONE,
        reference_date: date | None = None,
    ) -> ExtractionResult:
        calls.append(
            {
                "case_id": case_id,
                "field_name": field.name,
                "top_k": top_k,
                "privacy_mode": privacy_mode,
                "reference_date": reference_date,
            }
        )
        return responses[field.name]

    monkeypatch.setattr("app.orchestration.nodes.extract_field", _fake)
    return calls


def test_graph_calls_extract_field_once_per_schema_field_in_declared_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {
        "full_name": _ABSTAINED,
        "date_of_birth": _ABSTAINED,
        "pin_code": _ABSTAINED,
    }
    calls = _stub_extract_field(monkeypatch, responses)

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
    _stub_extract_field(monkeypatch, responses)

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
    _stub_extract_field(monkeypatch, responses)

    state = new_orchestration_state("case-3", _SCHEMA, PrivacyMode.NONE, _REFERENCE_DATE)
    final = run_graph(state)

    assert final.pending_field_names == []
    assert set(final.fields) == {"full_name", "date_of_birth", "pin_code"}


def test_graph_does_not_introduce_retries_or_verifier_traces_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {
        "full_name": ExtractionResult(
            value="Asha Rao", provenance=ExtractionProvenance(document_id="doc-1", page_number=1), confidence=0.95
        ),
        "date_of_birth": _ABSTAINED,
        "pin_code": _ABSTAINED,
    }
    _stub_extract_field(monkeypatch, responses)

    state = new_orchestration_state("case-4", _SCHEMA, PrivacyMode.NONE, _REFERENCE_DATE)
    final = run_graph(state)

    for field_state in final.fields.values():
        assert field_state.retry_count == 0
        assert field_state.verifier_traces == []


def test_graph_preserves_case_level_fields_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {"full_name": _ABSTAINED, "date_of_birth": _ABSTAINED, "pin_code": _ABSTAINED}
    _stub_extract_field(monkeypatch, responses)

    state = new_orchestration_state("case-5", _SCHEMA, PrivacyMode.POLICY_ENGINE, _REFERENCE_DATE)
    final = run_graph(state)

    assert final.case_id == "case-5"
    assert final.form_schema.id == _SCHEMA.id
    assert final.privacy_mode is PrivacyMode.POLICY_ENGINE
    assert final.reference_date == _REFERENCE_DATE
