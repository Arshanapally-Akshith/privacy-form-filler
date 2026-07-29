"""Node functions (`ARCHITECTURE.md` §7; `BUILD.md` Phase 5, tasks 2-6).

`extract_next_field` wraps `app.extraction.extractor.extract_field` unchanged -- same
arguments (`case_id`, `field`, `top_k`, `privacy_mode`, `reference_date`), same return
handling -- as `app.api.cases._process_case`'s hand-written loop, which this graph
replaced in commit 4.

`verify_current_field` wraps `app.orchestration.verifier.verify_field`, run immediately
after `extract_next_field` (or, on a retry, after `retry_current_field`) for the same
field (`current_field_name` is how this node knows which field without re-deriving it).
It re-retrieves the field's evidence via `retrieve_for_field` -- the same function
`extract_field` already calls internally, with the same `field.label`/`top_k`, so it is
guaranteed to see the identical evidence set extraction did -- rather than changing
`extract_field`'s signature to also return its evidence. That is a deliberate, stated
trade-off (one extra retrieval call per field, not free) made specifically to keep
`extract_field` unchanged and the extractor unaware this graph exists at all, consistent
with commit 3's "Graph -> Nodes -> Extractor" dependency direction.

`apply_verifier_decision` is the graph's one and only place that turns a `VerifierDecision`
into a `FieldState` change or a `retry_count` update (`ARCH §7`'s routing table,
`DECISIONS.md` E6). `verify_field` itself returns only a decision and reasoning -- it makes
no state-transition or routing decision of its own; this node and
`app.orchestration.graph`'s conditional edges own all of that, per `ARCH §7`.

`retry_current_field` re-extracts `current_field_name` using `RETRY_RETRIEVAL_TOP_K` (E18)
instead of `DEFAULT_RETRIEVAL_TOP_K` -- the wider evidence set is used only on this path,
never on the initial attempt. It runs only when `apply_verifier_decision` has just
incremented `retry_count`, i.e. only when the bounded retry budget (E6) still allows it.

Dependency direction is one-directional throughout this module: it imports
`app.extraction.extractor` and `app.orchestration.verifier`, never the reverse. Neither
`extract_field` nor `verify_field` takes graph-specific types or knows a LangGraph node
calls it.

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
from app.orchestration.constants import RETRY_BUDGET, RETRY_RETRIEVAL_TOP_K
from app.orchestration.state import (
    FieldGraphState,
    OrchestrationState,
    VerifierDecision,
)
from app.orchestration.verifier import verify_field
from app.retrieval.retriever import retrieve_for_field

EXTRACT_NEXT_FIELD_NODE = "extract_next_field"
VERIFY_CURRENT_FIELD_NODE = "verify_current_field"
APPLY_VERIFIER_DECISION_NODE = "apply_verifier_decision"
RETRY_CURRENT_FIELD_NODE = "retry_current_field"


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
    # retry_count/verifier_traces are preserved, not reset -- this node only ever runs
    # once per field (the initial attempt; `retry_current_field` handles re-extraction),
    # but preserving rather than overwriting them here is what makes that true regardless.
    updated_fields[field_name] = FieldGraphState(
        record=_to_field_record(field_spec, result),
        retry_count=existing.retry_count,
        verifier_traces=existing.verifier_traces,
    )

    return {
        "pending_field_names": remaining,
        "fields": updated_fields,
        "current_field_name": field_name,
    }


def verify_current_field(state: OrchestrationState) -> dict[str, Any]:
    """Verify `current_field_name` (set once by `extract_next_field`; unchanged by a
    retry, since `retry_current_field` re-extracts the same field) and append the
    resulting `VerifierTrace` -- persisted immediately, from this node's first commit
    (`ARCH §7.1`: "from the first commit"). This node only ever appends a trace;
    `apply_verifier_decision`, which always runs immediately after it, is what turns the
    decision into a `FieldState` change or a `retry_count` update."""
    field_name = state.current_field_name
    if field_name is None:
        raise AssertionError(
            "verify_current_field ran with no current_field_name set -- "
            "extract_next_field must always run immediately before this node"
        )
    field_spec = _field_spec_by_name(state.form_schema, field_name)
    field_graph_state = state.fields[field_name]

    evidence = retrieve_for_field(
        case_id=state.case_id, field_label=field_spec.label, top_k=DEFAULT_RETRIEVAL_TOP_K
    )
    trace = verify_field(
        case_id=state.case_id,
        field=field_spec,
        candidate_value=field_graph_state.record.value,
        candidate_confidence=field_graph_state.record.confidence,
        evidence=evidence,
        privacy_mode=state.privacy_mode,
        reference_date=state.reference_date,
    )

    updated_fields = dict(state.fields)
    updated_fields[field_name] = FieldGraphState(
        record=field_graph_state.record,
        retry_count=field_graph_state.retry_count,
        verifier_traces=[*field_graph_state.verifier_traces, trace],
    )

    return {"fields": updated_fields}


def apply_verifier_decision(state: OrchestrationState) -> dict[str, Any]:
    """Apply `current_field_name`'s most recent `VerifierTrace` decision -- the graph's
    one and only place that turns a decision into a `FieldState` change or a
    `retry_count` update (`ARCH §7`'s routing table; `DECISIONS.md` E6):

    - accept: no change. The record already reflects extraction's own result.
    - escalate: `FieldState.CONFLICT` -- a human decides, never a retry (`ARCH §7`).
    - re_retrieve, budget remaining (`retry_count < RETRY_BUDGET`): `retry_count += 1`.
      `FieldState` is left as-is; `retry_current_field` (routed next, by
      `app.orchestration.graph`) will replace the record with a fresh attempt.
    - re_retrieve, budget already exhausted (`retry_count == RETRY_BUDGET`):
      `FieldState.LOW_CONFIDENCE` -- flagged, never looped again (`DECISIONS.md` E6).
      This is also the signal `app.orchestration.graph._route_after_verifier_decision`
      reads to distinguish "just retried" from "budget exhausted," since both cases leave
      the latest trace's decision as `re_retrieve`.
    """
    field_name = state.current_field_name
    if field_name is None:
        raise AssertionError(
            "apply_verifier_decision ran with no current_field_name set -- "
            "verify_current_field must always run immediately before this node"
        )
    field_graph_state = state.fields[field_name]
    decision = field_graph_state.verifier_traces[-1].decision

    updated_fields = dict(state.fields)
    if decision is VerifierDecision.ESCALATE:
        updated_fields[field_name] = _with_record_state(field_graph_state, FieldState.CONFLICT)
    elif decision is VerifierDecision.RE_RETRIEVE:
        if field_graph_state.retry_count < RETRY_BUDGET:
            updated_fields[field_name] = FieldGraphState(
                record=field_graph_state.record,
                retry_count=field_graph_state.retry_count + 1,
                verifier_traces=field_graph_state.verifier_traces,
            )
        else:
            updated_fields[field_name] = _with_record_state(field_graph_state, FieldState.LOW_CONFIDENCE)
    # ACCEPT: no change -- updated_fields already has field_graph_state's current value
    # via dict(state.fields) above.

    return {"fields": updated_fields}


def retry_current_field(state: OrchestrationState) -> dict[str, Any]:
    """Re-extract `current_field_name` using `RETRY_RETRIEVAL_TOP_K` (E18) instead of
    `DEFAULT_RETRIEVAL_TOP_K` -- used only here, never on the initial attempt. Runs only
    when `apply_verifier_decision` has just incremented `retry_count`, so that increment
    is not repeated here; this node only replaces the record with a fresh extraction
    attempt, exactly as `extract_next_field` does, minus the `pending_field_names`/
    `current_field_name` bookkeeping (this field was already dequeued and is not
    re-queued -- retrying replaces its record in place)."""
    field_name = state.current_field_name
    if field_name is None:
        raise AssertionError(
            "retry_current_field ran with no current_field_name set -- "
            "apply_verifier_decision must always run immediately before this node"
        )
    field_spec = _field_spec_by_name(state.form_schema, field_name)
    existing = state.fields[field_name]

    result = extract_field(
        case_id=state.case_id,
        field=field_spec,
        top_k=RETRY_RETRIEVAL_TOP_K,
        privacy_mode=state.privacy_mode,
        reference_date=state.reference_date,
    )

    updated_fields = dict(state.fields)
    updated_fields[field_name] = FieldGraphState(
        record=_to_field_record(field_spec, result),
        retry_count=existing.retry_count,
        verifier_traces=existing.verifier_traces,
    )

    return {"fields": updated_fields}


def _with_record_state(field_graph_state: FieldGraphState, new_state: FieldState) -> FieldGraphState:
    return FieldGraphState(
        record=field_graph_state.record.model_copy(update={"state": new_state}),
        retry_count=field_graph_state.retry_count,
        verifier_traces=field_graph_state.verifier_traces,
    )


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
