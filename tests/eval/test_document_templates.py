"""Tests for the Phase 6 document template renderer (BUILD.md Phase 6 task 3). Pure-text
rendering from a fixed, seeded identity -- no I/O, no LLM, offline per CLAUDE.md §4 by
construction.

Negative ("this document must NOT contain that field's value") checks are deliberately
restricted to fields whose rendered form is distinctive enough to make an accidental
substring collision practically impossible: full_name (alphabetic), email_address
(contains "@"), pan_number (letters mixed into an otherwise all-digit field landscape),
formatted date_of_birth (contains "/"), and residential_address (a long descriptive
string). Pure-digit fields of varying length (aadhaar_number, phone_number, pin_code,
linked_account_number, annual_income) are deliberately *not* used in negative checks --
a short random digit string can coincidentally appear as a substring of an unrelated random
digit string often enough to make that assertion flaky, which would violate CLAUDE.md §4's
determinism requirement more than it would protect anything.
"""

import random
from datetime import date

from app.config.form_schema import load_form_schemas
from eval.dataset.document_templates import (
    ACADEMIC_RECORD,
    ADDRESS_PROOF,
    DOCUMENT_FIELD_SETS,
    ID_PROOF,
    INCOME_DOCUMENT,
    PRIMARY_SOURCE_DOCUMENT,
    render_academic_record,
    render_address_proof,
    render_document,
    render_id_proof,
    render_income_document,
)
from eval.dataset.identity_generator import Identity, generate_identity

REFERENCE_DATE = date(2026, 7, 29)


def _identity(seed: int = 11) -> Identity:
    return generate_identity(random.Random(seed), REFERENCE_DATE)


def _dob_str(identity: Identity) -> str:
    return identity.date_of_birth.strftime("%d/%m/%Y")


def test_id_proof_contains_exactly_its_intended_fields() -> None:
    identity = _identity()
    text = render_id_proof(identity)

    assert identity.full_name in text
    assert _dob_str(identity) in text
    assert identity.pan_number in text
    assert identity.aadhaar_number in text
    assert identity.phone_number in text
    assert identity.email_address in text

    # Distinctive-only negative check (see module docstring).
    assert identity.residential_address not in text


def test_address_proof_contains_exactly_its_intended_fields() -> None:
    identity = _identity()
    text = render_address_proof(identity)

    assert identity.residential_address in text
    assert identity.pin_code in text

    assert identity.full_name not in text
    assert identity.email_address not in text
    assert identity.pan_number not in text
    assert _dob_str(identity) not in text


def test_income_document_contains_exactly_its_intended_fields() -> None:
    identity = _identity()
    text = render_income_document(identity)

    assert str(identity.annual_income) in text
    assert identity.linked_account_number in text

    assert identity.full_name not in text
    assert identity.email_address not in text
    assert identity.pan_number not in text
    assert _dob_str(identity) not in text
    assert identity.residential_address not in text


def test_academic_record_corroborates_name_and_dob_without_new_targets() -> None:
    identity = _identity()
    text = render_academic_record(identity)

    assert identity.full_name in text
    assert _dob_str(identity) in text

    # No new extraction target introduced: no other identity field's distinctive form
    # leaks into the corroborating document.
    assert identity.email_address not in text
    assert identity.pan_number not in text
    assert identity.residential_address not in text


def test_academic_record_and_id_proof_agree_on_the_corroborated_fields() -> None:
    """The 'internally consistent' requirement, checked directly: wherever a field is
    deliberately repeated across documents (full_name, date_of_birth), every document that
    repeats it must carry the exact same value -- both come from the one Identity, so this
    should hold by construction, but it is the property Commit 6's dataset-wide consistency
    test will generalize, and it is worth proving here where it is cheap and obvious why it
    must hold."""
    identity = _identity()
    id_proof_text = render_id_proof(identity)
    academic_text = render_academic_record(identity)

    assert identity.full_name in id_proof_text
    assert identity.full_name in academic_text
    assert _dob_str(identity) in id_proof_text
    assert _dob_str(identity) in academic_text


def test_document_field_sets_cover_every_primary_source_field_exactly_once() -> None:
    for field, document_type in PRIMARY_SOURCE_DOCUMENT.items():
        assert field in DOCUMENT_FIELD_SETS[document_type]


def test_academic_record_overlap_with_id_proof_is_the_only_deliberate_overlap() -> None:
    document_types = list(DOCUMENT_FIELD_SETS)
    for i, left in enumerate(document_types):
        for right in document_types[i + 1 :]:
            overlap = DOCUMENT_FIELD_SETS[left] & DOCUMENT_FIELD_SETS[right]
            expected_overlap = {"full_name", "date_of_birth"} if {left, right} == {ID_PROOF, ACADEMIC_RECORD} else set()
            assert overlap == expected_overlap, f"unexpected field overlap between {left} and {right}: {overlap}"


def test_no_template_field_is_outside_the_real_form_schemas() -> None:
    """Mechanical guard on 'no new extraction targets': every field name any template
    embeds must be a real field on at least one committed form schema."""
    schema_field_names = {field.name for schema in load_form_schemas() for field in schema.fields}
    template_field_names = {field for fields in DOCUMENT_FIELD_SETS.values() for field in fields}

    assert template_field_names <= schema_field_names


def test_rendering_is_deterministic_for_a_fixed_identity() -> None:
    identity = _identity(seed=99)

    for document_type in DOCUMENT_FIELD_SETS:
        assert render_document(document_type, identity) == render_document(document_type, identity)


def test_rendering_is_deterministic_for_a_fixed_seed_end_to_end() -> None:
    identity_a = generate_identity(random.Random(123), REFERENCE_DATE)
    identity_b = generate_identity(random.Random(123), REFERENCE_DATE)

    for document_type in DOCUMENT_FIELD_SETS:
        assert render_document(document_type, identity_a) == render_document(document_type, identity_b)


def test_render_document_dispatches_to_the_matching_renderer() -> None:
    identity = _identity()

    assert render_document(ID_PROOF, identity) == render_id_proof(identity)
    assert render_document(ADDRESS_PROOF, identity) == render_address_proof(identity)
    assert render_document(INCOME_DOCUMENT, identity) == render_income_document(identity)
    assert render_document(ACADEMIC_RECORD, identity) == render_academic_record(identity)
