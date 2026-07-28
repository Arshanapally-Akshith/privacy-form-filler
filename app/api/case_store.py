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
from datetime import date

from app.api.models import CaseStatus, FieldRecord
from app.boundary.mode import PrivacyMode


@dataclass
class CaseRecord:
    case_id: str
    form_schema_id: str
    status: CaseStatus
    # DECISIONS.md E1: age is generalized against the form's submission/creation date, not
    # the source documents' dates. Captured once, here, at case creation, so every later
    # generalize_dob call for this case reads the same value rather than re-deriving it.
    submitted_at: date
    # BUILD.md Phase 4 commit 2: the ablation mechanism. Fixed for the case's lifetime --
    # set once at creation, read by _process_case for every field, never changed mid-flight.
    privacy_mode: PrivacyMode
    fields: dict[str, FieldRecord] = field(default_factory=dict)  # insertion order = schema field order


class CaseStore:
    def __init__(self) -> None:
        self._cases: dict[str, CaseRecord] = {}

    def create(
        self,
        case_id: str,
        form_schema_id: str,
        submitted_at: date | None = None,
        privacy_mode: PrivacyMode = PrivacyMode.NONE,
    ) -> CaseRecord:
        # Real, request-time creation date -- not a test reading the clock (CLAUDE.md §4
        # is about tests, not production code). Mirrors app.privacy.cli's own noqa
        # precedent below, for the same "no timezone, DOB is date-only" reasoning.
        resolved_submitted_at = submitted_at if submitted_at is not None else date.today()  # noqa: DTZ011
        record = CaseRecord(
            case_id=case_id,
            form_schema_id=form_schema_id,
            status=CaseStatus.PROCESSING,
            submitted_at=resolved_submitted_at,
            privacy_mode=privacy_mode,
        )
        self._cases[case_id] = record
        return record

    def get(self, case_id: str) -> CaseRecord | None:
        return self._cases.get(case_id)


# Process-global, in-memory (DECISIONS.md R14), mirroring app.retrieval.store.case_index_registry.
case_store = CaseStore()
