"""Node functions (`ARCHITECTURE.md` §7; `BUILD.md` Phase 5, task 2).

`extract_next_field` wraps `app.extraction.extractor.extract_field` unchanged -- same
arguments (`case_id`, `field`, `top_k`, `privacy_mode`, `reference_date`), same return
handling -- as `app.api.cases._process_case`'s hand-written loop, which this graph is
built to replace in commit 4.

Dependency direction is one-directional: this module imports `app.extraction.extractor`,
never the reverse. `extract_field` takes no graph-specific types, returns its own
`ExtractionResult` unchanged, and has no knowledge that a LangGraph node calls it -- it is
called here exactly as `app.api.cases._process_case` already calls it today.

Node functions return a partial-update `dict` rather than mutating `state` in place. This
is not a style preference: LangGraph applies a plain `LastValue` (whole-value overwrite)
channel to any state field without an explicit reducer, so a node must return the *full*
new value for any field it changes -- verified directly against the installed
langgraph==1.2.9 rather than assumed (a node's incoming `state` argument is a proper
`OrchestrationState` instance; `CompiledStateGraph.invoke()`'s return value is a plain
`dict` of final channel values regardless of the state schema type -- see
`app.orchestration.graph`, which reconstructs a typed `OrchestrationState` from it).

`_to_field_record` below is a deliberate, temporary duplicate of
`app.api.cases._to_field_record`: this commit does not touch `app/api/cases.py` (the
standalone graph is not wired into the API yet), so the two copies coexist briefly.
Commit 4 deletes `cases.py`'s copy when it swaps `_process_case` for this graph, leaving
this one as the only implementation.
"""

from typing import Any

from app.api.models import FieldRecord, FieldState, Provenance
from app.config.form_schema import FormFieldSpec, FormSchema
from app.extraction.constants import DEFAULT_RETRIEVAL_TOP_K
from app.extraction.extractor import ExtractionResult, extract_field
from app.orchestration.state import FieldGraphState, OrchestrationState

EXTRACT_NEXT_FIELD_NODE = "extract_next_field"


def extract_next_field(state: OrchestrationState) -> dict[str, Any]:
    """Extract the field at the front of `pending_field_names`, in the same order the
    form schema declares its fields -- identical traversal order to
    `app.api.cases._process_case`'s `for schema_field in schema.fields` loop, since
    `new_orchestration_state` seeds `pending_field_names` from that same order."""
    field_name = state.pending_field_names[0]
    remaining = state.pending_field_names[1:]
    field_spec = _field_spec_by_name(state.form_schema, field_name)

    result = extract_field(
        case_id=state.case_id,
        field=field_spec,
        top_k=DEFAULT_RETRIEVAL_TOP_K,
        privacy_mode=state.privacy_mode,
        reference_date=state.reference_date,
    )

    updated_fields = dict(state.fields)
    existing = updated_fields[field_name]
    # retry_count/verifier_traces are preserved, not reset -- always 0/[] as of this
    # commit (nothing sets them yet), but extraction is re-invoked on retry from commit 6
    # onward, and a retry must not wipe the bookkeeping it is itself part of.
    updated_fields[field_name] = FieldGraphState(
        record=_to_field_record(field_spec, result),
        retry_count=existing.retry_count,
        verifier_traces=existing.verifier_traces,
    )

    return {"pending_field_names": remaining, "fields": updated_fields}


def _field_spec_by_name(form_schema: FormSchema, field_name: str) -> FormFieldSpec:
    for spec in form_schema.fields:
        if spec.name == field_name:
            return spec
    raise AssertionError(
        f"Field {field_name!r} not found in form schema {form_schema.id!r} -- "
        "pending_field_names must always be a subset of the schema's own field names"
    )


def _to_field_record(field: FormFieldSpec, result: ExtractionResult) -> FieldRecord:
    if result.value is None:
        return FieldRecord(name=field.name, label=field.label, state=FieldState.MISSING)

    if result.provenance is None:
        raise AssertionError(
            f"ExtractionResult for field {field.name!r} has a value but no provenance -- "
            "ExtractionResult invariant violated upstream"
        )
    return FieldRecord(
        name=field.name,
        label=field.label,
        value=result.value,
        confidence=result.confidence,
        state=FieldState.FILLED,
        provenance=Provenance(
            document_id=result.provenance.document_id, page_number=result.provenance.page_number
        ),
    )
