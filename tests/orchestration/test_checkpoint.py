"""Checkpointing and resume tests (`BUILD.md` Phase 5, task 8; `DECISIONS.md` C1/R16).

"Crash" is simulated as a node raising mid-run -- a genuine interruption, not a deliberate
pause -- and "resume" is proven through the checkpointer's *persisted state*, keyed by
`thread_id`, rather than by reusing the same Python `CompiledStateGraph` object: after the
crash, a second, freshly-built compiled graph (same `InMemorySaver` instance, same
`thread_id`) resumes the run. This is the honestly-scoped meaning of "crash-resume" in a
single-container deployment where sibling state (the pseudonym map, the vector index) is
already process-memory-only by design (`DECISIONS.md` R8/R14) -- surviving an actual
process restart is explicitly out of scope (R16). No SQLite or other durable backend is
used or needed to prove this.
"""

from datetime import date

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

from app.api.models import FieldState
from app.boundary.mode import PrivacyMode
from app.config.form_schema import FieldType, FormFieldSpec, FormSchema
from app.extraction.constants import DEFAULT_RETRIEVAL_TOP_K
from app.extraction.extractor import ExtractionProvenance, ExtractionResult
from app.orchestration.graph import build_graph, resume_graph, run_graph
from app.orchestration.state import VerifierDecision, new_orchestration_state
from app.orchestration.verifier import _VerifierResponse
from app.retrieval.retriever import RetrievedEvidence

_REFERENCE_DATE = date(2026, 1, 1)

_SCHEMA = FormSchema(
    id="checkpoint_test_form",
    name="Checkpoint Test Form",
    description="Three-field schema for checkpoint/resume tests.",
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

_STUB_EVIDENCE = [
    RetrievedEvidence(document_id="doc-1", page_number=1, chunk_index=0, text="stub evidence", score=0.5)
]


class _SimulatedCrash(RuntimeError):
    """A distinct exception type, so a test can assert specifically *this* is what
    interrupted the run, not some unrelated failure."""


def _stub_pipeline(monkeypatch: pytest.MonkeyPatch, fail_on: set[str]) -> list[str]:
    """`fail_on` is a mutable set the test can clear between the crash and the resume, to
    simulate "whatever caused the crash is now resolved" -- e.g. the same transient
    provider failure `DECISIONS.md` §5 Phase 4 already documented. Returns the list of
    field names extraction was actually invoked for, in order."""
    extraction_calls: list[str] = []

    def _fake_extract(
        case_id: str,
        field: FormFieldSpec,
        top_k: int = DEFAULT_RETRIEVAL_TOP_K,
        privacy_mode: PrivacyMode = PrivacyMode.NONE,
        reference_date: date | None = None,
    ) -> ExtractionResult:
        if field.name in fail_on:
            raise _SimulatedCrash(f"simulated crash extracting {field.name!r}")
        extraction_calls.append(field.name)
        return ExtractionResult(
            value=f"value-for-{field.name}",
            provenance=ExtractionProvenance(document_id="doc-1", page_number=1),
            confidence=0.9,
        )

    def _fake_retrieve(case_id: str, field_label: str, top_k: int) -> list[RetrievedEvidence]:
        return _STUB_EVIDENCE

    def _fake_verify(prompt: str, response_schema: type, **kwargs: object) -> _VerifierResponse:
        return _VerifierResponse(decision=VerifierDecision.ACCEPT, reasoning="stub")

    monkeypatch.setattr("app.orchestration.nodes.extract_field", _fake_extract)
    monkeypatch.setattr("app.orchestration.nodes.retrieve_for_field", _fake_retrieve)
    monkeypatch.setattr("app.orchestration.verifier.generate_structured_protected", _fake_verify)
    return extraction_calls


def test_run_graph_without_a_checkpointer_behaves_exactly_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    """Checkpointing is strictly opt-in -- the default (no `checkpointer` argument, as
    every pre-commit-8 caller uses it) must be unaffected."""
    extraction_calls = _stub_pipeline(monkeypatch, fail_on=set())

    state = new_orchestration_state("case-no-checkpoint", _SCHEMA, PrivacyMode.NONE, _REFERENCE_DATE)
    final = run_graph(state)

    assert extraction_calls == ["full_name", "date_of_birth", "pin_code"]
    assert final.pending_field_names == []


def test_resume_via_run_graph_and_resume_graph_does_not_reprocess_completed_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_id = "case-checkpoint-resume"
    fail_on = {"date_of_birth"}
    extraction_calls = _stub_pipeline(monkeypatch, fail_on)

    saver = InMemorySaver()
    state = new_orchestration_state(case_id, _SCHEMA, PrivacyMode.NONE, _REFERENCE_DATE)

    with pytest.raises(_SimulatedCrash):
        run_graph(state, checkpointer=saver)

    # Only the field before the crash point ever completed.
    assert extraction_calls == ["full_name"]

    # The checkpoint genuinely persisted that partial progress -- inspected directly,
    # not inferred from the resume succeeding.
    thread_config: RunnableConfig = {"configurable": {"thread_id": case_id}}
    checkpointed = build_graph(checkpointer=saver).get_state(thread_config)
    assert checkpointed.values["fields"]["full_name"].record.state == FieldState.FILLED
    assert checkpointed.values["fields"]["date_of_birth"].record.state == FieldState.MISSING

    # Whatever "crashed" it is now resolved -- resume from the checkpoint.
    fail_on.clear()
    final = resume_graph(case_id, checkpointer=saver)

    # full_name was NOT re-extracted; only the two fields that hadn't completed yet ran.
    assert extraction_calls == ["full_name", "date_of_birth", "pin_code"]

    assert final.fields["full_name"].record.state == FieldState.FILLED
    assert final.fields["full_name"].record.value == "value-for-full_name"
    assert final.fields["date_of_birth"].record.state == FieldState.FILLED
    assert final.fields["pin_code"].record.state == FieldState.FILLED
    assert final.pending_field_names == []


def test_resume_uses_a_freshly_built_compiled_graph_not_the_original_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point this test exists to prove: resumption works because the checkpointer
    resolves state by `thread_id`, not because any particular `CompiledStateGraph` Python
    object is reused. A second `StateGraph(...).compile(checkpointer=saver)` -- built from
    scratch, never having seen the first run -- must still resume correctly."""
    case_id = "case-checkpoint-identity"
    fail_on = {"date_of_birth"}
    extraction_calls = _stub_pipeline(monkeypatch, fail_on)

    saver = InMemorySaver()
    state = new_orchestration_state(case_id, _SCHEMA, PrivacyMode.NONE, _REFERENCE_DATE)
    thread_config: RunnableConfig = {"configurable": {"thread_id": case_id}}

    first_compiled = build_graph(checkpointer=saver)
    with pytest.raises(_SimulatedCrash):
        first_compiled.invoke(state, config=thread_config)

    assert extraction_calls == ["full_name"]

    fail_on.clear()
    second_compiled = build_graph(checkpointer=saver)
    assert second_compiled is not first_compiled  # genuinely a different object

    result = second_compiled.invoke(None, config=thread_config)

    assert extraction_calls == ["full_name", "date_of_birth", "pin_code"]
    assert result["pending_field_names"] == []
    assert result["fields"]["full_name"].record.state == FieldState.FILLED
    assert result["fields"]["date_of_birth"].record.state == FieldState.FILLED
    assert result["fields"]["pin_code"].record.state == FieldState.FILLED
