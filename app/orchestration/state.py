"""Graph state schema (`ARCHITECTURE.md` §7; `BUILD.md` Phase 5, task 1).

ARCH §7 names exactly what the graph state carries: case ID, form schema, per-field
records (value, confidence, provenance, state), retry counts, and verifier reasoning
traces. This module defines that shape and nothing beyond it -- no graph, no nodes, no
edges yet (those are later Phase 5 commits); this is purely the type contract every node
and edge in this phase will read and write.

Per-field state reuses `app.api.models.FieldRecord` / `FieldState` rather than inventing a
parallel representation: the record is the single source of truth for "what this field's
result is," shared by the graph and the API layer alike, so the two can never drift apart
into two different notions of "done." `FieldGraphState` adds only what `FieldRecord` has no
reason to carry -- retry count and verifier traces are graph-execution bookkeeping, not
API-facing result data.

Processing is sequential, one field at a time -- matching how `app.extraction.extractor
.extract_field` and the Phase 2 `_process_case` loop it replaces both already work. No
concurrent field processing, so no LangGraph reducer / `Annotated[...]` merge logic is
needed here: a node updates exactly one field's `FieldGraphState` per step.
`pending_field_names` is the cursor a later graph loop consumes to know which field is
next, in schema-declaration order.

`case_id` doubles as the boundary layer's `session_id` (see
`app.extraction.extractor.extract_field`, which already passes `session_id=case_id` to
`generate_structured_protected`) -- there is deliberately no separate `session_id` field
here.
"""

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from app.api.models import FieldRecord, FieldState
from app.boundary.mode import PrivacyMode
from app.config.form_schema import FormSchema
from app.retrieval.retriever import RetrievedEvidence


class VerifierDecision(str, Enum):
    """`ARCH §7.1`, `DECISIONS.md` R6 -- the verifier's only three possible decisions."""

    ACCEPT = "accept"
    RE_RETRIEVE = "re_retrieve"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class VerifierTrace:
    """One verifier decision for one field, persisted immediately when made (`ARCH §7.1`:
    "from the first commit" -- required input to the Phase 6/7 verifier audit;
    retrofitting trace persistence later would mean re-running everything). Immutable: a
    trace is a record of a decision already made, never edited after the fact.

    `evidence` is the full retrieved set the verifier was shown, not just whichever chunk
    extraction happened to cite -- reusing `app.retrieval.retriever.RetrievedEvidence`
    rather than a duplicate type, since it already carries everything a persisted trace
    needs to reconstruct what the verifier saw (document, page, chunk, text).
    """

    field_name: str
    decision: VerifierDecision
    reasoning: str
    evidence: tuple[RetrievedEvidence, ...]


@dataclass
class FieldGraphState:
    """Graph-local wrapper around one field's `FieldRecord`. `retry_count` and
    `verifier_traces` are bookkeeping the graph needs to enforce the bounded-retry
    invariant (`DECISIONS.md` E6) and persist verifier reasoning; neither belongs on
    `FieldRecord` itself, which the API layer also constructs directly and has no notion of
    retries or verifier decisions.
    """

    record: FieldRecord
    retry_count: int = 0
    verifier_traces: list[VerifierTrace] = field(default_factory=list)


@dataclass
class OrchestrationState:
    """Whole-case graph state -- exactly the fields `ARCH §7` names (case ID, form schema,
    per-field records, retry counts, verifier traces), plus the minimum bookkeeping two
    graph nodes need to cooperate on one field in sequence: `pending_field_names` (which
    field is next) and `current_field_name` (which field the most recent extraction was
    for, so the verification step that follows it -- a separate graph node, per `BUILD.md`
    Phase 5 commits 5/6 -- knows which field to verify without re-deriving it). Nothing
    else is added ahead of need.
    """

    case_id: str
    form_schema: FormSchema
    privacy_mode: PrivacyMode
    reference_date: date
    fields: dict[str, FieldGraphState] = field(default_factory=dict)
    pending_field_names: list[str] = field(default_factory=list)
    current_field_name: str | None = None


def new_orchestration_state(
    case_id: str,
    form_schema: FormSchema,
    privacy_mode: PrivacyMode,
    reference_date: date,
) -> OrchestrationState:
    """Build the initial state for a fresh case. Every field starts `FieldState.MISSING`
    (I5 -- nothing has been extracted yet; `MISSING` is the correct starting state, not a
    placeholder standing in for something else), with no retries spent and no verifier
    traces, in the form schema's declared field order.
    """
    fields = {
        spec.name: FieldGraphState(
            record=FieldRecord(name=spec.name, label=spec.label, state=FieldState.MISSING)
        )
        for spec in form_schema.fields
    }
    return OrchestrationState(
        case_id=case_id,
        form_schema=form_schema,
        privacy_mode=privacy_mode,
        reference_date=reference_date,
        fields=fields,
        pending_field_names=[spec.name for spec in form_schema.fields],
    )
