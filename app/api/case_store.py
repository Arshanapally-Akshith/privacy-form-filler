"""In-memory case store (BUILD.md Phase 2, task 7).

Mirrors app.retrieval.store's CaseIndexRegistry pattern exactly (DECISIONS.md R8/R14:
process memory, not durable across restarts -- a container restart loses all cases and
requires re-creation, same as the vector index). Unlike app.extraction/app.retrieval/
app.filling, this module IS the API layer, so depending on app.api.models directly is not
a boundary violation -- it is the natural owner of that state.

A CaseRecord may exist internally with status=FAILED for a case whose creation request
ultimately returned an error to the caller (a provider failure during processing) -- kept
for server-side diagnostics, per design. The caller is never handed that case_id in a
success response, so this is not a resource "created" from the client's perspective.
"""

from dataclasses import dataclass, field

from app.api.models import CaseStatus, FieldRecord


@dataclass
class CaseRecord:
    case_id: str
    form_schema_id: str
    status: CaseStatus
    fields: dict[str, FieldRecord] = field(default_factory=dict)  # insertion order = schema field order


class CaseStore:
    def __init__(self) -> None:
        self._cases: dict[str, CaseRecord] = {}

    def create(self, case_id: str, form_schema_id: str) -> CaseRecord:
        record = CaseRecord(case_id=case_id, form_schema_id=form_schema_id, status=CaseStatus.PROCESSING)
        self._cases[case_id] = record
        return record

    def get(self, case_id: str) -> CaseRecord | None:
        return self._cases.get(case_id)


# Process-global, in-memory (DECISIONS.md R14), mirroring app.retrieval.store.case_index_registry.
case_store = CaseStore()
