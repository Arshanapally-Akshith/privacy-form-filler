"""Tests for the Phase 6 case assembler and adversarial transforms (BUILD.md Phase 6 tasks
4-5). Pure in-memory case construction from a seeded RNG -- no I/O, no LLM, offline per
CLAUDE.md §4 by construction.
"""

import random
from datetime import date

import pytest

from app.config.form_schema import FormSchema, load_form_schemas
from app.privacy.generalize import parse_date_of_birth
from eval.dataset.case_builder import (
    ACADEMIC_RECORD,
    ADDRESS_PROOF,
    ID_PROOF,
    INCOME_DOCUMENT,
    NOMINEE_DECLARATION,
    Case,
    build_clean_case,
    build_conflicting_case,
    build_missing_field_case,
    build_near_duplicate_name_case,
    build_unusual_format_case,
)

REFERENCE_DATE = date(2026, 7, 29)

_SCHEMAS = {schema.id: schema for schema in load_form_schemas()}
KYC_SCHEMA: FormSchema = _SCHEMAS["kyc_account_opening"]
INSURANCE_SCHEMA: FormSchema = _SCHEMAS["insurance_policy_application"]


def _text_for(case: Case, document_type: str) -> str:
    matches = [d.text for d in case.documents if d.document_type == document_type]
    assert matches, f"no document of type {document_type!r} in case {case.case_id!r}"
    return matches[0]


# ---------------------------------------------------------------------------
# Clean (non-adversarial) baseline
# ---------------------------------------------------------------------------


def test_clean_case_ground_truth_matches_identity_for_every_field() -> None:
    case = build_clean_case(random.Random(1), "case-1", KYC_SCHEMA, REFERENCE_DATE)
    identity = case.primary_identity

    assert case.ground_truth["full_name"] == identity.full_name
    assert case.ground_truth["date_of_birth"] == identity.date_of_birth.strftime("%d/%m/%Y")
    assert case.ground_truth["pan_number"] == identity.pan_number
    assert case.ground_truth["aadhaar_number"] == identity.aadhaar_number
    assert case.ground_truth["residential_address"] == identity.residential_address
    assert case.ground_truth["pin_code"] == identity.pin_code
    assert case.ground_truth["phone_number"] == identity.phone_number
    assert case.ground_truth["email_address"] == identity.email_address
    assert case.ground_truth["linked_account_number"] == identity.linked_account_number
    assert case.ground_truth["annual_income"] == str(identity.annual_income)
    assert case.adversarial is None


def test_clean_case_documents_are_internally_consistent_on_the_corroborated_fields() -> None:
    case = build_clean_case(random.Random(2), "case-2", KYC_SCHEMA, REFERENCE_DATE)
    identity = case.primary_identity
    dob_str = identity.date_of_birth.strftime("%d/%m/%Y")

    id_proof_text = _text_for(case, ID_PROOF)
    academic_text = _text_for(case, ACADEMIC_RECORD)
    assert identity.full_name in id_proof_text
    assert identity.full_name in academic_text
    assert dob_str in id_proof_text
    assert dob_str in academic_text


def test_clean_case_adds_nominee_document_only_for_insurance_schema() -> None:
    kyc_case = build_clean_case(random.Random(3), "case-kyc", KYC_SCHEMA, REFERENCE_DATE)
    insurance_case = build_clean_case(random.Random(3), "case-ins", INSURANCE_SCHEMA, REFERENCE_DATE)

    assert not any(d.document_type == NOMINEE_DECLARATION for d in kyc_case.documents)
    assert "nominee_full_name" not in kyc_case.ground_truth

    nominee_docs = [d for d in insurance_case.documents if d.document_type == NOMINEE_DECLARATION]
    assert len(nominee_docs) == 1
    assert insurance_case.secondary_identities  # the nominee identity
    nominee_identity = insurance_case.secondary_identities[0]
    assert insurance_case.ground_truth["nominee_full_name"] == nominee_identity.full_name
    assert nominee_identity.full_name in nominee_docs[0].text


def test_clean_case_is_deterministic_for_a_fixed_seed() -> None:
    case_a = build_clean_case(random.Random(42), "case-a", INSURANCE_SCHEMA, REFERENCE_DATE)
    case_b = build_clean_case(random.Random(42), "case-a", INSURANCE_SCHEMA, REFERENCE_DATE)

    assert case_a == case_b


# ---------------------------------------------------------------------------
# Conflicting values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["full_name", "date_of_birth"])
def test_conflicting_documents_genuinely_disagree(field: str) -> None:
    case = build_conflicting_case(random.Random(5), "case-conflict", KYC_SCHEMA, REFERENCE_DATE, field=field)
    detail = case.adversarial
    assert detail is not None
    assert detail.detail["primary_value"] != detail.detail["conflicting_value"]

    id_proof_text = _text_for(case, ID_PROOF)
    academic_text = _text_for(case, ACADEMIC_RECORD)
    assert detail.detail["primary_value"] in id_proof_text
    assert detail.detail["conflicting_value"] not in id_proof_text
    assert detail.detail["conflicting_value"] in academic_text
    assert detail.detail["primary_value"] not in academic_text


def test_conflicting_case_ground_truth_is_the_canonical_value_never_null() -> None:
    case = build_conflicting_case(random.Random(6), "case-conflict-gt", KYC_SCHEMA, REFERENCE_DATE, field="date_of_birth")

    assert case.ground_truth["date_of_birth"] == case.primary_identity.date_of_birth.strftime("%d/%m/%Y")
    assert case.ground_truth["date_of_birth"] is not None
    assert case.adversarial is not None
    assert case.adversarial.adversarial_type == "conflict"
    assert case.adversarial.detail["field"] == "date_of_birth"


def test_conflicting_case_rejects_unsupported_field() -> None:
    with pytest.raises(ValueError, match="Unsupported conflict field"):
        build_conflicting_case(random.Random(7), "case-bad", KYC_SCHEMA, REFERENCE_DATE, field="pan_number")


def test_conflicting_case_is_deterministic_for_a_fixed_seed() -> None:
    case_a = build_conflicting_case(random.Random(9), "case-a", KYC_SCHEMA, REFERENCE_DATE)
    case_b = build_conflicting_case(random.Random(9), "case-a", KYC_SCHEMA, REFERENCE_DATE)

    assert case_a == case_b


# ---------------------------------------------------------------------------
# Missing required field
# ---------------------------------------------------------------------------


def test_missing_field_case_truly_omits_the_value_from_every_document() -> None:
    case = build_missing_field_case(
        random.Random(11), "case-missing", KYC_SCHEMA, REFERENCE_DATE, omitted_document_type=ADDRESS_PROOF
    )
    identity = case.primary_identity

    assert case.ground_truth["residential_address"] is None
    assert case.ground_truth["pin_code"] is None
    assert not any(d.document_type == ADDRESS_PROOF for d in case.documents)
    for document in case.documents:
        assert identity.residential_address not in document.text
        assert identity.pin_code not in document.text


def test_missing_field_case_leaves_other_fields_evidenced() -> None:
    case = build_missing_field_case(
        random.Random(12), "case-missing-2", KYC_SCHEMA, REFERENCE_DATE, omitted_document_type=ADDRESS_PROOF
    )

    assert case.ground_truth["full_name"] == case.primary_identity.full_name
    assert case.ground_truth["pan_number"] == case.primary_identity.pan_number


def test_missing_field_case_is_tagged() -> None:
    case = build_missing_field_case(
        random.Random(13), "case-missing-3", KYC_SCHEMA, REFERENCE_DATE, omitted_document_type=ADDRESS_PROOF
    )
    assert case.adversarial is not None
    assert case.adversarial.adversarial_type == "missing_field"
    assert case.adversarial.detail["omitted_document_type"] == ADDRESS_PROOF
    assert "residential_address" in case.adversarial.detail["omitted_required_fields"].split(",")


def test_missing_field_case_rejects_omission_that_removes_no_required_field() -> None:
    # income_document's fields (annual_income, linked_account_number) are both optional on
    # the KYC schema -- omitting it must not silently produce a case that isn't actually a
    # required-field-absence case.
    with pytest.raises(ValueError, match="does not remove any required field"):
        build_missing_field_case(
            random.Random(14), "case-bad", KYC_SCHEMA, REFERENCE_DATE, omitted_document_type=INCOME_DOCUMENT
        )


def test_missing_field_case_accepts_income_document_omission_for_insurance() -> None:
    # annual_income is required on the insurance schema, so this omission IS valid there.
    case = build_missing_field_case(
        random.Random(15), "case-ins-missing", INSURANCE_SCHEMA, REFERENCE_DATE, omitted_document_type=INCOME_DOCUMENT
    )
    assert case.ground_truth["annual_income"] is None


def test_missing_field_case_rejects_unsupported_document_type() -> None:
    with pytest.raises(ValueError, match="Unsupported omitted_document_type"):
        build_missing_field_case(
            random.Random(16), "case-bad-2", KYC_SCHEMA, REFERENCE_DATE, omitted_document_type=ID_PROOF
        )


def test_missing_field_case_is_deterministic_for_a_fixed_seed() -> None:
    case_a = build_missing_field_case(random.Random(17), "case-a", KYC_SCHEMA, REFERENCE_DATE, omitted_document_type=ADDRESS_PROOF)
    case_b = build_missing_field_case(random.Random(17), "case-a", KYC_SCHEMA, REFERENCE_DATE, omitted_document_type=ADDRESS_PROOF)

    assert case_a == case_b


# ---------------------------------------------------------------------------
# Near-duplicate names
# ---------------------------------------------------------------------------


def test_near_duplicate_case_contains_two_distinct_but_similar_identities() -> None:
    case = build_near_duplicate_name_case(random.Random(21), "case-dup", KYC_SCHEMA, REFERENCE_DATE)
    assert case.adversarial is not None
    primary_name = case.adversarial.detail["primary_name"]
    duplicate_name = case.adversarial.detail["duplicate_name"]

    assert primary_name != duplicate_name

    primary_first, _, primary_last = primary_name.partition(" ")
    duplicate_first, _, duplicate_last = duplicate_name.partition(" ")
    assert primary_last == duplicate_last  # same surname
    assert primary_first != duplicate_first  # but not the same first name
    assert primary_first[:-1] == duplicate_first[:-1]  # differs by exactly one trailing character


def test_near_duplicate_case_ground_truth_still_matches_the_primary_identity() -> None:
    case = build_near_duplicate_name_case(random.Random(22), "case-dup-2", KYC_SCHEMA, REFERENCE_DATE)

    assert case.ground_truth["full_name"] == case.primary_identity.full_name
    assert case.ground_truth["date_of_birth"] == case.primary_identity.date_of_birth.strftime("%d/%m/%Y")


def test_near_duplicate_case_duplicate_document_does_not_source_any_field() -> None:
    case = build_near_duplicate_name_case(random.Random(23), "case-dup-3", KYC_SCHEMA, REFERENCE_DATE)
    academic_docs = [d for d in case.documents if d.document_type == ACADEMIC_RECORD]
    assert len(academic_docs) == 1  # only the duplicate's -- the primary's own slot was replaced

    duplicate_identity = case.secondary_identities[0]
    assert duplicate_identity.full_name in academic_docs[0].text
    assert case.primary_identity.full_name not in academic_docs[0].text


def test_near_duplicate_case_is_tagged() -> None:
    case = build_near_duplicate_name_case(random.Random(24), "case-dup-4", KYC_SCHEMA, REFERENCE_DATE)
    assert case.adversarial is not None
    assert case.adversarial.adversarial_type == "near_duplicate_name"


def test_near_duplicate_case_is_deterministic_for_a_fixed_seed() -> None:
    case_a = build_near_duplicate_name_case(random.Random(25), "case-a", KYC_SCHEMA, REFERENCE_DATE)
    case_b = build_near_duplicate_name_case(random.Random(25), "case-a", KYC_SCHEMA, REFERENCE_DATE)

    assert case_a == case_b


# ---------------------------------------------------------------------------
# Unusual formats
# ---------------------------------------------------------------------------


def test_unusual_format_date_of_birth_preserves_semantic_equivalence() -> None:
    case = build_unusual_format_case(
        random.Random(31), "case-format-dob", KYC_SCHEMA, REFERENCE_DATE, field="date_of_birth"
    )
    assert case.adversarial is not None
    detail = case.adversarial.detail
    assert detail["document_format_value"] != detail["canonical_ground_truth_value"]

    assert parse_date_of_birth(detail["document_format_value"]) == parse_date_of_birth(detail["canonical_ground_truth_value"])
    assert detail["document_format_value"] in _text_for(case, ID_PROOF)
    assert case.ground_truth["date_of_birth"] == detail["canonical_ground_truth_value"]


def test_unusual_format_phone_number_preserves_semantic_equivalence() -> None:
    case = build_unusual_format_case(
        random.Random(32), "case-format-phone", KYC_SCHEMA, REFERENCE_DATE, field="phone_number"
    )
    assert case.adversarial is not None
    detail = case.adversarial.detail

    assert detail["document_format_value"] == f"+91-{detail['canonical_ground_truth_value']}"
    assert detail["document_format_value"] in _text_for(case, ID_PROOF)
    assert case.ground_truth["phone_number"] == detail["canonical_ground_truth_value"]
    assert case.ground_truth["phone_number"] == case.primary_identity.phone_number


def test_unusual_format_case_rejects_unsupported_field() -> None:
    with pytest.raises(ValueError, match="Unsupported unusual-format field"):
        build_unusual_format_case(random.Random(33), "case-bad", KYC_SCHEMA, REFERENCE_DATE, field="pin_code")


def test_unusual_format_case_is_deterministic_for_a_fixed_seed() -> None:
    case_a = build_unusual_format_case(random.Random(34), "case-a", KYC_SCHEMA, REFERENCE_DATE)
    case_b = build_unusual_format_case(random.Random(34), "case-a", KYC_SCHEMA, REFERENCE_DATE)

    assert case_a == case_b


# ---------------------------------------------------------------------------
# Cross-cutting: every case carries the correct adversarial metadata
# ---------------------------------------------------------------------------


def test_every_builder_tags_its_case_with_the_expected_adversarial_type() -> None:
    clean = build_clean_case(random.Random(41), "c", KYC_SCHEMA, REFERENCE_DATE)
    conflict = build_conflicting_case(random.Random(41), "c", KYC_SCHEMA, REFERENCE_DATE)
    missing = build_missing_field_case(random.Random(41), "c", KYC_SCHEMA, REFERENCE_DATE, omitted_document_type=ADDRESS_PROOF)
    duplicate = build_near_duplicate_name_case(random.Random(41), "c", KYC_SCHEMA, REFERENCE_DATE)
    unusual = build_unusual_format_case(random.Random(41), "c", KYC_SCHEMA, REFERENCE_DATE)

    assert clean.adversarial is None
    assert conflict.adversarial is not None and conflict.adversarial.adversarial_type == "conflict"
    assert missing.adversarial is not None and missing.adversarial.adversarial_type == "missing_field"
    assert duplicate.adversarial is not None and duplicate.adversarial.adversarial_type == "near_duplicate_name"
    assert unusual.adversarial is not None and unusual.adversarial.adversarial_type == "unusual_format"
