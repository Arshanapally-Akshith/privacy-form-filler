"""HTTP-level tests for the case API (BUILD.md Phase 2, task 7).

The end-to-end smoke test and the abstention-continuity test are written first: BUILD.md
names their behavior explicitly ("upload -> PDF, no exceptions, all fields have a state";
I5/G4 "a required value genuinely absent must return missing, not a value"). The rest is
written alongside, covering the wiring decisions made in this task's plan: synchronous
processing, the two failure classes on creation, the FieldState mapping, and the
human-review provenance marker.

No real LLM or embedding calls -- both are monkeypatched (CLAUDE.md §4). A small,
purpose-built two-field form schema is injected into cases._FORM_SCHEMAS_BY_ID for most
tests, rather than exercising the full ten/eight-field committed schemas, to keep each
test's LLM stub trivial to reason about. Synthetic PDFs are built inline via fitz at test
time, mirroring tests/ingest/conftest.py's existing pattern -- no fixture files.
"""

import re

import fitz
import httpx
import pytest
from fastapi.testclient import TestClient

import app.api.cases as cases_module
from app.api.case_store import case_store
from app.api.factory import create_app
from app.api.models import CaseStatus
from app.config.form_schema import FieldType, FormFieldSpec, FormSchema
from app.extraction.extractor import _FieldExtractionResponse
from app.extraction.llm_client import LLMProviderError
from app.orchestration.state import VerifierDecision
from app.orchestration.verifier import _VerifierResponse

_TEST_SCHEMA_ID = "test_two_field_form"
_TOKENIZE_SCHEMA_ID = "test_two_identifier_field_form"


def _uniform_vector(texts: list[str]) -> list[list[float]]:
    return [[1.0, 0.0] for _ in texts]


def _pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 72), text, fontsize=12)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


@pytest.fixture
def two_field_schema(monkeypatch: pytest.MonkeyPatch) -> FormSchema:
    """A minimal schema: one field the stub LLM will find, one it will abstain on."""
    schema = FormSchema(
        id=_TEST_SCHEMA_ID,
        name="Test Two Field Form",
        description="Minimal schema for case API tests.",
        fields=[
            FormFieldSpec(
                name="full_name", label="Full Name", type=FieldType.NAME, required=True, policy_action_ref="name"
            ),
            FormFieldSpec(
                name="aadhaar_number",
                label="Aadhaar Number",
                type=FieldType.IDENTIFIER,
                required=True,
                policy_action_ref="aadhaar",
            ),
        ],
    )
    monkeypatch.setitem(cases_module._FORM_SCHEMAS_BY_ID, _TEST_SCHEMA_ID, schema)
    return schema


@pytest.fixture
def two_identifier_field_schema(monkeypatch: pytest.MonkeyPatch) -> FormSchema:
    """Both fields are Tokenize-compatible entity types (PAN, ACCOUNT_NUMBER) -- used only
    by the privacy_mode=full_tokenize tests below, so the known NAME-type gap
    (two_field_schema's full_name field, tested separately) never enters into them.
    two_field_schema itself is untouched by this fixture."""
    schema = FormSchema(
        id=_TOKENIZE_SCHEMA_ID,
        name="Test Two Identifier Field Form",
        description="Minimal Tokenize-compatible schema for privacy_mode=full_tokenize tests.",
        fields=[
            FormFieldSpec(
                name="pan_number", label="PAN Number", type=FieldType.IDENTIFIER, required=True, policy_action_ref="pan"
            ),
            FormFieldSpec(
                name="account_number",
                label="Account Number",
                type=FieldType.IDENTIFIER,
                required=True,
                policy_action_ref="account_number",
            ),
        ],
    )
    monkeypatch.setitem(cases_module._FORM_SCHEMAS_BY_ID, _TOKENIZE_SCHEMA_ID, schema)
    return schema


def _stub_llm_finds_name_abstains_on_aadhaar(
    prompt: str, response_schema: type, **kwargs: object
) -> _FieldExtractionResponse:
    if "Field: Full Name." in prompt:
        return _FieldExtractionResponse(value="Asha Rao", source_chunk_index=0, confidence=0.92)
    return _FieldExtractionResponse(value=None, source_chunk_index=None, confidence=None)


def _stub_verifier_always_accepts(prompt: str, response_schema: type, **kwargs: object) -> _VerifierResponse:
    """Since commit 5, every extracted field is also verified -- a second, independent
    call through `generate_structured_protected` (`app.orchestration.verifier`'s own
    import binding, distinct from `app.extraction.extractor`'s). Tests that stub
    extraction must stub this too, or the verifier's call falls through to the real
    provider (`CLAUDE.md` §4 forbids that)."""
    return _VerifierResponse(decision=VerifierDecision.ACCEPT, reasoning="stub verifier: always accept")


def _create_case(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, schema_id: str = _TEST_SCHEMA_ID
) -> httpx.Response:
    monkeypatch.setattr("app.retrieval.store.embed_texts", _uniform_vector)
    monkeypatch.setattr("app.retrieval.retriever.embed_texts", _uniform_vector)
    monkeypatch.setattr(
        "app.extraction.extractor.generate_structured_protected", _stub_llm_finds_name_abstains_on_aadhaar
    )
    monkeypatch.setattr("app.orchestration.verifier.generate_structured_protected", _stub_verifier_always_accepts)

    return client.post(
        "/api/cases",
        data={"form_schema_id": schema_id},
        files=[("documents", ("id_proof.pdf", _pdf_bytes("Applicant Full Name: Asha Rao"), "application/pdf"))],
    )


def test_end_to_end_smoke_upload_to_pdf(two_field_schema: FormSchema, monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(create_app())

    create_response = _create_case(client, monkeypatch)
    assert create_response.status_code == 201
    case_id = create_response.json()["case_id"]
    assert create_response.json()["status"] == "completed"

    fields_response = client.get(f"/api/cases/{case_id}/fields")
    assert fields_response.status_code == 200
    fields = fields_response.json()["fields"]
    assert len(fields) == 2
    assert {f["state"] for f in fields} <= {"filled", "missing"}

    result_response = client.get(f"/api/cases/{case_id}/result")
    assert result_response.status_code == 200
    assert result_response.headers["content-type"] == "application/pdf"
    assert result_response.content.startswith(b"%PDF")


def test_field_with_no_supporting_evidence_returns_missing_not_a_value(
    two_field_schema: FormSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(create_app())
    case_id = _create_case(client, monkeypatch).json()["case_id"]

    fields = {f["name"]: f for f in client.get(f"/api/cases/{case_id}/fields").json()["fields"]}

    assert fields["aadhaar_number"]["state"] == "missing"
    assert fields["aadhaar_number"]["value"] is None
    assert fields["aadhaar_number"]["provenance"] is None

    pdf_text = _extract_pdf_text(client.get(f"/api/cases/{case_id}/result").content)
    assert "Aadhaar Number" in pdf_text
    assert "Missing" in pdf_text


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def test_create_case_unknown_form_schema_returns_404() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/cases",
        data={"form_schema_id": "not_a_real_schema"},
        files=[("documents", ("id.pdf", _pdf_bytes("text"), "application/pdf"))],
    )
    assert response.status_code == 404


def test_create_case_no_documents_returns_422(two_field_schema: FormSchema) -> None:
    """`documents` is a required field with no default -- FastAPI's own request validation
    rejects a request with none supplied before the handler ever runs, the same convention
    `DebugRetrieveRequest` already established for other required fields."""
    client = TestClient(create_app())
    response = client.post("/api/cases", data={"form_schema_id": _TEST_SCHEMA_ID}, files=[])
    assert response.status_code == 422


def test_create_case_unsupported_document_type_returns_400(two_field_schema: FormSchema) -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/cases",
        data={"form_schema_id": _TEST_SCHEMA_ID},
        files=[("documents", ("notes.txt", b"plain text", "text/plain"))],
    )
    assert response.status_code == 400


def test_provider_failure_returns_502_and_preserves_internal_failed_state(
    two_field_schema: FormSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(create_app())
    monkeypatch.setattr("app.retrieval.store.embed_texts", _uniform_vector)
    monkeypatch.setattr("app.retrieval.retriever.embed_texts", _uniform_vector)

    def _raise(prompt: str, response_schema: type, **kwargs: object) -> _FieldExtractionResponse:
        raise LLMProviderError("simulated provider outage")

    monkeypatch.setattr("app.extraction.extractor.generate_structured_protected", _raise)

    existing_ids = set(case_store._cases.keys())
    response = client.post(
        "/api/cases",
        data={"form_schema_id": _TEST_SCHEMA_ID},
        files=[("documents", ("id_proof.pdf", _pdf_bytes("Applicant Full Name: Asha Rao"), "application/pdf"))],
    )

    assert response.status_code == 502
    assert "case_id" not in response.json()

    new_ids = set(case_store._cases.keys()) - existing_ids
    assert len(new_ids) == 1
    failed_case_id = new_ids.pop()
    failed_record = case_store._cases[failed_case_id]
    assert failed_record.status == CaseStatus.FAILED

    # Not reachable by a client that never learned the case_id, but still diagnosable
    # server-side: status and result both correctly reflect the failure, not a phantom
    # success.
    status_response = client.get(f"/api/cases/{failed_case_id}/status")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "failed"

    result_response = client.get(f"/api/cases/{failed_case_id}/result")
    assert result_response.status_code == 400


def test_get_status_unknown_case_returns_404() -> None:
    client = TestClient(create_app())
    assert client.get("/api/cases/never-created/status").status_code == 404


def test_get_status_reports_full_progress_once_completed(
    two_field_schema: FormSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(create_app())
    case_id = _create_case(client, monkeypatch).json()["case_id"]

    response = client.get(f"/api/cases/{case_id}/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["progress"] == {"fields_total": 2, "fields_completed": 2}


def test_get_fields_unknown_case_returns_404() -> None:
    client = TestClient(create_app())
    assert client.get("/api/cases/never-created/fields").status_code == 404


def test_get_fields_returns_records_in_schema_order(
    two_field_schema: FormSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(create_app())
    case_id = _create_case(client, monkeypatch).json()["case_id"]

    fields = client.get(f"/api/cases/{case_id}/fields").json()["fields"]

    assert [f["name"] for f in fields] == ["full_name", "aadhaar_number"]


def test_review_approve_keeps_value_and_sets_human_reviewed_state(
    two_field_schema: FormSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(create_app())
    case_id = _create_case(client, monkeypatch).json()["case_id"]

    response = client.post(f"/api/cases/{case_id}/fields/full_name/review", json={"decision": "approve"})

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "human_reviewed"
    assert body["value"] == "Asha Rao"


def test_review_override_replaces_value_with_synthetic_provenance(
    two_field_schema: FormSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(create_app())
    case_id = _create_case(client, monkeypatch).json()["case_id"]

    response = client.post(
        f"/api/cases/{case_id}/fields/aadhaar_number/review",
        json={"decision": "override", "value": "123456789012"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "human_reviewed"
    assert body["value"] == "123456789012"
    assert body["confidence"] is None
    assert body["provenance"] == {"document_id": "human_review", "page_number": 0}


def test_review_override_without_value_returns_422(
    two_field_schema: FormSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(create_app())
    case_id = _create_case(client, monkeypatch).json()["case_id"]

    response = client.post(f"/api/cases/{case_id}/fields/aadhaar_number/review", json={"decision": "override"})

    assert response.status_code == 422


def test_review_unknown_case_returns_404() -> None:
    client = TestClient(create_app())
    response = client.post("/api/cases/never-created/fields/full_name/review", json={"decision": "approve"})
    assert response.status_code == 404


def test_review_unknown_field_returns_404(two_field_schema: FormSchema, monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(create_app())
    case_id = _create_case(client, monkeypatch).json()["case_id"]

    response = client.post(f"/api/cases/{case_id}/fields/not_a_real_field/review", json={"decision": "approve"})

    assert response.status_code == 404


def test_get_result_unknown_case_returns_404() -> None:
    client = TestClient(create_app())
    assert client.get("/api/cases/never-created/result").status_code == 404


# --- privacy_mode (BUILD.md Phase 4, commit 5) --------------------------------------------


def test_create_case_default_privacy_mode_is_none(
    two_field_schema: FormSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(create_app())
    response = _create_case(client, monkeypatch)
    assert response.json()["privacy_mode"] == "none"


def test_create_case_policy_engine_returns_501_and_creates_no_case(two_field_schema: FormSchema) -> None:
    """policy_engine is a real, pinned mode (DECISIONS.md V1), just not implemented yet
    (BUILD.md Phase 4 commit 6) -- rejected before any document is touched, same as the
    other deterministic, request-time rejections above."""
    client = TestClient(create_app())
    existing_ids = set(case_store._cases.keys())

    response = client.post(
        "/api/cases",
        data={"form_schema_id": _TEST_SCHEMA_ID, "privacy_mode": "policy_engine"},
        files=[("documents", ("id.pdf", _pdf_bytes("text"), "application/pdf"))],
    )

    assert response.status_code == 501
    assert set(case_store._cases.keys()) == existing_ids


def test_create_case_invalid_privacy_mode_returns_422(two_field_schema: FormSchema) -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/cases",
        data={"form_schema_id": _TEST_SCHEMA_ID, "privacy_mode": "not_a_real_mode"},
        files=[("documents", ("id.pdf", _pdf_bytes("text"), "application/pdf"))],
    )
    assert response.status_code == 422


def _stub_llm_echoes_pan_from_evidence(
    prompt: str, response_schema: type
) -> _FieldExtractionResponse | _VerifierResponse:
    """Patches `app.boundary.llm.generate_structured` -- the low-level call shared by
    every caller of `generate_structured_protected` regardless of which module's own
    import binding invoked it, so this one stub now serves both extraction's and (since
    commit 5) the verifier's calls and must branch on `response_schema` to answer each
    correctly."""
    if response_schema is _VerifierResponse:
        return _VerifierResponse(decision=VerifierDecision.ACCEPT, reasoning="stub verifier: always accept")
    if "Field: PAN Number." in prompt:
        # Whatever the evidence actually says at this point -- under full_tokenize this is
        # the *token*, not the real PAN, exactly as a real LLM would only ever see what was
        # sent to it.
        match = re.search(r"PAN Number:\s*(\S+)", prompt)
        assert match is not None, "evidence text did not reach the prompt"
        return _FieldExtractionResponse(value=match.group(1), source_chunk_index=0, confidence=0.9)
    return _FieldExtractionResponse(value=None, source_chunk_index=None, confidence=None)


def test_full_tokenize_mode_returns_the_original_value_not_the_token(
    two_identifier_field_schema: FormSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BUILD.md Phase 4 testing requirement: "reversal integration -- values returned to
    the user are original, not tokens." Only the real network call
    (app.boundary.llm.generate_structured) is stubbed here, letting protect()/reverse() run
    for real -- this proves the full round trip through the actual API, distinct from
    tests/boundary/test_llm.py's isolated coverage of the same mechanism."""
    client = TestClient(create_app())
    monkeypatch.setattr("app.retrieval.store.embed_texts", _uniform_vector)
    monkeypatch.setattr("app.retrieval.retriever.embed_texts", _uniform_vector)
    monkeypatch.setattr("app.boundary.llm.generate_structured", _stub_llm_echoes_pan_from_evidence)

    response = client.post(
        "/api/cases",
        data={"form_schema_id": _TOKENIZE_SCHEMA_ID, "privacy_mode": "full_tokenize"},
        files=[
            (
                "documents",
                ("id_proof.pdf", _pdf_bytes("Applicant PAN Number: ABCDE1234F"), "application/pdf"),
            )
        ],
    )

    assert response.status_code == 201
    assert response.json()["privacy_mode"] == "full_tokenize"
    case_id = response.json()["case_id"]

    fields = {f["name"]: f for f in client.get(f"/api/cases/{case_id}/fields").json()["fields"]}
    assert fields["pan_number"]["value"] == "ABCDE1234F"


def test_full_tokenize_mode_with_unsupported_entity_type_returns_500_and_preserves_failed_state(
    two_field_schema: FormSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NAME has no FF1 alphabet (app.privacy.tokenize) -- an existing, already-documented
    Phase 3 gap. Under privacy_mode=full_tokenize this must fail cleanly, not crash
    unhandled -- mirrors test_provider_failure_returns_502_and_preserves_internal_failed_state's
    shape for this distinct failure class (500, not 502: an internal engine limitation, not
    an upstream provider outage)."""
    client = TestClient(create_app())
    monkeypatch.setattr("app.retrieval.store.embed_texts", _uniform_vector)
    monkeypatch.setattr("app.retrieval.retriever.embed_texts", _uniform_vector)

    def _fail_if_called(prompt: str, response_schema: type) -> _FieldExtractionResponse:
        raise AssertionError("protect() should raise before the LLM is ever called")

    monkeypatch.setattr("app.boundary.llm.generate_structured", _fail_if_called)

    existing_ids = set(case_store._cases.keys())
    response = client.post(
        "/api/cases",
        data={"form_schema_id": _TEST_SCHEMA_ID, "privacy_mode": "full_tokenize"},
        files=[("documents", ("id_proof.pdf", _pdf_bytes("Applicant Full Name: Asha Rao"), "application/pdf"))],
    )

    assert response.status_code == 500
    assert "case_id" not in response.json()

    new_ids = set(case_store._cases.keys()) - existing_ids
    assert len(new_ids) == 1
    failed_record = case_store._cases[new_ids.pop()]
    assert failed_record.status == CaseStatus.FAILED
