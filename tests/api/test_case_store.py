"""Unit tests for app.api.case_store (BUILD.md Phase 4, commit 1).

CaseRecord.submitted_at is DECISIONS.md E1's reference date -- the form's submission/
creation date, captured once at case creation so every later generalize_dob call for this
case reads the same value. Per CLAUDE.md §4 ("no reliance on wall-clock time"), the default
path is checked only for type, never for an exact date.today() value.
"""

from datetime import date

from app.api.case_store import CaseStore
from app.api.models import CaseStatus


def test_create_stores_explicit_submitted_at() -> None:
    store = CaseStore()
    submitted_at = date(2026, 1, 15)

    record = store.create("case-1", "schema-1", submitted_at=submitted_at)

    assert record.submitted_at == submitted_at
    assert store.get("case-1").submitted_at == submitted_at


def test_create_defaults_submitted_at_to_a_real_date() -> None:
    store = CaseStore()

    record = store.create("case-2", "schema-1")

    assert isinstance(record.submitted_at, date)


def test_submitted_at_is_stable_across_later_mutation() -> None:
    store = CaseStore()
    submitted_at = date(2026, 1, 15)
    record = store.create("case-3", "schema-1", submitted_at=submitted_at)

    record.status = CaseStatus.COMPLETED

    assert record.submitted_at == submitted_at
