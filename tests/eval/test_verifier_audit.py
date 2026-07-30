"""Unit tests for eval.harness.verifier_audit's pure case/field-selection logic (Phase 7
Commit 9). Per this commit's own scope, the live-call path (run_audit_case, run_audit,
main) is never exercised here -- no mocking of the model, no network, nothing live, and no
precision/recall computation either (deliberately deferred to a later, separate
interpretation commit). Synthetic case dicts only, shaped like real
eval/dataset/phase6_eval_cases.json entries but never the real file.
"""

import pytest

from eval.harness.verifier_audit import (
    UnknownAdversarialTypeError,
    select_audit_cases,
    select_audit_field,
)


def _clean_case(case_id: str) -> dict[str, object]:
    return {"case_id": case_id, "adversarial": None}


def _adversarial_case(case_id: str, adversarial_type: str, detail: dict[str, object]) -> dict[str, object]:
    return {"case_id": case_id, "adversarial": {"adversarial_type": adversarial_type, "detail": detail}}


# ---------------------------------------------------------------------------
# select_audit_field
# ---------------------------------------------------------------------------


def test_clean_case_selects_the_fixed_clean_case_field() -> None:
    assert select_audit_field(_clean_case("kyc_clean_001")) == "full_name"


def test_conflict_case_selects_the_recorded_conflicting_field() -> None:
    case = _adversarial_case(
        "kyc_conflict_001",
        "conflict",
        {"field": "date_of_birth", "primary_value": "17/03/1967", "conflicting_value": "17/03/1962"},
    )
    assert select_audit_field(case) == "date_of_birth"


def test_unusual_format_case_selects_the_recorded_reformatted_field() -> None:
    case = _adversarial_case(
        "insurance_unusual_format_002",
        "unusual_format",
        {"field": "phone_number", "canonical_ground_truth_value": "6560410750", "document_format_value": "+91-6560410750"},
    )
    assert select_audit_field(case) == "phone_number"


def test_missing_field_case_selects_the_first_omitted_required_field() -> None:
    case = _adversarial_case(
        "kyc_missing_field_001",
        "missing_field",
        {"omitted_document_type": "address_proof", "omitted_required_fields": "pin_code,residential_address"},
    )
    assert select_audit_field(case) == "pin_code"


def test_missing_field_case_with_a_single_omitted_field() -> None:
    case = _adversarial_case(
        "insurance_missing_field_002",
        "missing_field",
        {"omitted_document_type": "income_document", "omitted_required_fields": "annual_income"},
    )
    assert select_audit_field(case) == "annual_income"


def test_near_duplicate_name_case_always_selects_full_name() -> None:
    case = _adversarial_case(
        "kyc_near_duplicate_001",
        "near_duplicate_name",
        {"duplicate_document_type": "academic_record", "duplicate_name": "Nehd Sharma", "primary_name": "Neha Sharma"},
    )
    assert select_audit_field(case) == "full_name"


def test_unrecognized_adversarial_type_raises_rather_than_guessing() -> None:
    case = _adversarial_case("some_case", "some_future_adversarial_type", {})
    with pytest.raises(UnknownAdversarialTypeError):
        select_audit_field(case)


# ---------------------------------------------------------------------------
# select_audit_cases
# ---------------------------------------------------------------------------


def test_select_audit_cases_includes_every_adversarial_case() -> None:
    all_cases = [
        _clean_case("clean-1"),
        _adversarial_case("adv-1", "conflict", {"field": "date_of_birth"}),
        _adversarial_case("adv-2", "unusual_format", {"field": "phone_number"}),
        _clean_case("clean-2"),
    ]
    selected = select_audit_cases(all_cases, clean_case_count=1)
    selected_ids = {case["case_id"] for case in selected}
    assert {"adv-1", "adv-2"} <= selected_ids


def test_select_audit_cases_limits_clean_cases_to_the_requested_count() -> None:
    all_cases = [_clean_case(f"clean-{i}") for i in range(10)]
    selected = select_audit_cases(all_cases, clean_case_count=3)
    assert len(selected) == 3
    assert [case["case_id"] for case in selected] == ["clean-0", "clean-1", "clean-2"]


def test_select_audit_cases_preserves_dataset_order_for_clean_cases() -> None:
    """Not curated or reordered toward any particular outcome -- clean cases are taken in
    whatever order the input list already has them, per this commit's own "representative,
    not engineered" requirement."""
    all_cases = [_clean_case("clean-z"), _clean_case("clean-a"), _clean_case("clean-m")]
    selected = select_audit_cases(all_cases, clean_case_count=2)
    assert [case["case_id"] for case in selected] == ["clean-z", "clean-a"]


def test_select_audit_cases_total_count_matches_adversarial_plus_clean() -> None:
    all_cases = [_adversarial_case(f"adv-{i}", "conflict", {"field": "date_of_birth"}) for i in range(12)] + [
        _clean_case(f"clean-{i}") for i in range(5)
    ]
    selected = select_audit_cases(all_cases, clean_case_count=3)
    assert len(selected) == 15  # 12 adversarial + 3 clean, matching DECISIONS.md V11's range
