"""Extraction node tests (BUILD.md Phase 2, tasks 3-5).

Abstention and provenance-correctness are the two tests BUILD.md names explicitly as
written before implementation for this phase -- abstention because "it is the one an
interviewer will probe" (I5/G4: no evidence -> missing, never invented), provenance because
a citation that does not actually contain the value is worse than no citation. No real LLM
or embedding calls -- both are monkeypatched, per CLAUDE.md §4.

Every test whose name contains "abstention" enforces CLAUDE.md's protected Invariant I5
(BUILD.md Phase 2 task 5) -- none may be weakened, skipped, or deleted to make a change
pass; a failure here means the code is wrong, not the test.
"""

import pytest
from pydantic import ValidationError

from app.config.form_schema import FieldType, FormFieldSpec, load_form_schemas
from app.extraction.extractor import _FieldExtractionResponse, extract_field
from app.extraction.llm_client import LLMProviderError
from app.ingest.chunker import Chunk
from app.retrieval.retriever import UnknownCaseError
from app.retrieval.store import case_index_registry, embed_chunks

_FIELD = FormFieldSpec(
    name="pan_number",
    label="PAN Number",
    type=FieldType.IDENTIFIER,
    required=True,
    expected_format="5 letters, 4 digits, 1 letter",
    policy_action_ref="pan",
)


def _uniform_vector(texts: list[str]) -> list[list[float]]:
    return [[1.0, 0.0] for _ in texts]


def _seed_case(monkeypatch: pytest.MonkeyPatch, case_id: str, chunks: list[Chunk]) -> None:
    monkeypatch.setattr("app.retrieval.store.embed_texts", _uniform_vector)
    case_index_registry.get_or_create(case_id).add(embed_chunks(chunks))
    monkeypatch.setattr("app.retrieval.retriever.embed_texts", _uniform_vector)


def test_no_evidence_at_all_returns_abstention_without_calling_the_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_id = "extractor-case-no-evidence"
    case_index_registry.get_or_create(case_id)  # known case, nothing indexed
    monkeypatch.setattr("app.retrieval.retriever.embed_texts", _uniform_vector)

    def _fail_if_called(prompt: str, response_schema: type) -> None:
        raise AssertionError("LLM must not be called when there is no retrieved evidence")

    monkeypatch.setattr("app.extraction.extractor.generate_structured", _fail_if_called)

    result = extract_field(case_id=case_id, field=_FIELD)

    assert result.value is None
    assert result.provenance is None


def test_llm_abstention_on_unsupported_field_returns_missing_not_a_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_id = "extractor-case-unsupported"
    chunk = Chunk(document_id="doc-1", page_number=1, chunk_index=0, text="Name: Asha Rao")
    _seed_case(monkeypatch, case_id, [chunk])

    def _abstain(prompt: str, response_schema: type) -> _FieldExtractionResponse:
        return _FieldExtractionResponse(value=None, source_chunk_index=None, confidence=None)

    monkeypatch.setattr("app.extraction.extractor.generate_structured", _abstain)

    result = extract_field(case_id=case_id, field=_FIELD)

    assert result.value is None
    assert result.provenance is None
    assert result.confidence is None


def test_extracted_value_has_provenance_pointing_at_the_chunk_that_contains_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_id = "extractor-case-provenance"
    chunks = [
        Chunk(document_id="doc-cover", page_number=1, chunk_index=0, text="Application form cover page"),
        Chunk(document_id="doc-id-proof", page_number=2, chunk_index=0, text="PAN: ABCDE1234F"),
    ]
    _seed_case(monkeypatch, case_id, chunks)

    def _cite_second_chunk(prompt: str, response_schema: type) -> _FieldExtractionResponse:
        assert "ABCDE1234F" in prompt  # the evidence actually reached the prompt
        return _FieldExtractionResponse(value="ABCDE1234F", source_chunk_index=1, confidence=0.95)

    monkeypatch.setattr("app.extraction.extractor.generate_structured", _cite_second_chunk)

    result = extract_field(case_id=case_id, field=_FIELD, top_k=5)

    assert result.value == "ABCDE1234F"
    assert result.provenance is not None
    assert result.provenance.document_id == "doc-id-proof"
    assert result.provenance.page_number == 2
    assert result.confidence == pytest.approx(0.95)
    # Provenance correctness: the cited page's chunk actually contains the extracted value.
    cited_chunk = chunks[1]
    assert cited_chunk.document_id == result.provenance.document_id
    assert result.value in cited_chunk.text


def test_out_of_range_source_chunk_index_is_treated_as_abstention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_id = "extractor-case-bad-citation"
    chunk = Chunk(document_id="doc-1", page_number=1, chunk_index=0, text="PAN: ABCDE1234F")
    _seed_case(monkeypatch, case_id, [chunk])

    def _hallucinate_index(prompt: str, response_schema: type) -> _FieldExtractionResponse:
        return _FieldExtractionResponse(value="ABCDE1234F", source_chunk_index=7, confidence=0.9)

    monkeypatch.setattr("app.extraction.extractor.generate_structured", _hallucinate_index)

    result = extract_field(case_id=case_id, field=_FIELD)

    assert result.value is None
    assert result.provenance is None
    assert result.confidence is None


def test_unknown_case_propagates_rather_than_abstaining(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(UnknownCaseError):
        extract_field(case_id="extractor-case-never-created", field=_FIELD)


def test_llm_provider_failure_propagates_rather_than_abstaining(monkeypatch: pytest.MonkeyPatch) -> None:
    case_id = "extractor-case-llm-failure"
    chunk = Chunk(document_id="doc-1", page_number=1, chunk_index=0, text="PAN: ABCDE1234F")
    _seed_case(monkeypatch, case_id, [chunk])

    def _raise(prompt: str, response_schema: type) -> _FieldExtractionResponse:
        raise LLMProviderError("simulated provider outage")

    monkeypatch.setattr("app.extraction.extractor.generate_structured", _raise)

    with pytest.raises(LLMProviderError):
        extract_field(case_id=case_id, field=_FIELD)


def test_value_without_confidence_raises_rather_than_defaulting(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLAUDE.md §5: a broken provider contract (a value with no confidence score) fails
    loudly instead of silently defaulting to some assumed number."""
    case_id = "extractor-case-missing-confidence"
    chunk = Chunk(document_id="doc-1", page_number=1, chunk_index=0, text="PAN: ABCDE1234F")
    _seed_case(monkeypatch, case_id, [chunk])

    def _value_without_confidence(prompt: str, response_schema: type) -> _FieldExtractionResponse:
        return _FieldExtractionResponse(value="ABCDE1234F", source_chunk_index=0, confidence=None)

    monkeypatch.setattr("app.extraction.extractor.generate_structured", _value_without_confidence)

    with pytest.raises(LLMProviderError):
        extract_field(case_id=case_id, field=_FIELD)


@pytest.mark.parametrize("bad_confidence", [-0.01, 1.01, 2.0])
def test_out_of_range_confidence_is_rejected_at_the_schema_level(bad_confidence: float) -> None:
    """DECISIONS.md E7: bounds are enforced on the structured-output schema itself, so an
    out-of-range score fails parsing inside generate_structured() rather than being
    silently clamped."""
    with pytest.raises(ValidationError):
        _FieldExtractionResponse(value="ABCDE1234F", source_chunk_index=0, confidence=bad_confidence)


def test_empty_string_value_is_treated_as_abstention(monkeypatch: pytest.MonkeyPatch) -> None:
    """I5/G4: a non-null but empty string is not a value -- it must abstain exactly like a
    null value, not pass through as a "filled" result."""
    case_id = "extractor-case-empty-string-value"
    chunk = Chunk(document_id="doc-1", page_number=1, chunk_index=0, text="Name: Asha Rao")
    _seed_case(monkeypatch, case_id, [chunk])

    def _empty_value(prompt: str, response_schema: type) -> _FieldExtractionResponse:
        return _FieldExtractionResponse(value="", source_chunk_index=0, confidence=0.5)

    monkeypatch.setattr("app.extraction.extractor.generate_structured", _empty_value)

    result = extract_field(case_id=case_id, field=_FIELD)

    assert result.value is None
    assert result.provenance is None
    assert result.confidence is None


def test_whitespace_only_value_is_treated_as_abstention(monkeypatch: pytest.MonkeyPatch) -> None:
    """I5/G4: whitespace is not a value, same treatment as an empty string."""
    case_id = "extractor-case-whitespace-value"
    chunk = Chunk(document_id="doc-1", page_number=1, chunk_index=0, text="Name: Asha Rao")
    _seed_case(monkeypatch, case_id, [chunk])

    def _whitespace_value(prompt: str, response_schema: type) -> _FieldExtractionResponse:
        return _FieldExtractionResponse(value="   \t  ", source_chunk_index=0, confidence=0.5)

    monkeypatch.setattr("app.extraction.extractor.generate_structured", _whitespace_value)

    result = extract_field(case_id=case_id, field=_FIELD)

    assert result.value is None
    assert result.provenance is None
    assert result.confidence is None


def test_extracted_value_is_trimmed_of_surrounding_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    """A genuine value is normalized (trimmed) exactly once before being returned, not just
    before being checked for emptiness."""
    case_id = "extractor-case-untrimmed-value"
    chunk = Chunk(document_id="doc-1", page_number=1, chunk_index=0, text="PAN: ABCDE1234F")
    _seed_case(monkeypatch, case_id, [chunk])

    def _padded_value(prompt: str, response_schema: type) -> _FieldExtractionResponse:
        return _FieldExtractionResponse(value="  ABCDE1234F  ", source_chunk_index=0, confidence=0.9)

    monkeypatch.setattr("app.extraction.extractor.generate_structured", _padded_value)

    result = extract_field(case_id=case_id, field=_FIELD)

    assert result.value == "ABCDE1234F"


def test_abstention_holds_for_a_field_from_the_committed_insurance_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The abstention path is not specific to the synthetic _FIELD fixture -- it holds for a
    field drawn from one of the two real R9 schemas."""
    schemas = {schema.id: schema for schema in load_form_schemas()}
    nominee_field = next(
        f for f in schemas["insurance_policy_application"].fields if f.name == "nominee_full_name"
    )

    case_id = "extractor-case-insurance-nominee-missing"
    chunk = Chunk(document_id="doc-1", page_number=1, chunk_index=0, text="Applicant's own ID proof only")
    _seed_case(monkeypatch, case_id, [chunk])

    def _abstain(prompt: str, response_schema: type) -> _FieldExtractionResponse:
        return _FieldExtractionResponse(value=None, source_chunk_index=None, confidence=None)

    monkeypatch.setattr("app.extraction.extractor.generate_structured", _abstain)

    result = extract_field(case_id=case_id, field=nominee_field)

    assert result.value is None
    assert result.provenance is None
    assert result.confidence is None
