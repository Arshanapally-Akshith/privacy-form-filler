"""Document template renderer for the Phase 6 evaluation dataset (BUILD.md Phase 6 task 3).

Four document types, shared across both form schemas (ARCHITECTURE.md D13, "shared
document pool ... keeps two forms affordable"): ID proof, address proof, income document,
academic record. Each is a pure function of a single eval.dataset.identity_generator.Identity
-- rendering is entirely deterministic for a fixed identity; no additional randomness is
introduced at this layer.

**Field-to-document mapping is deliberately disjoint** across the first three templates --
each identity field has exactly one designated primary-evidence document, recorded in
DOCUMENT_FIELD_SETS / PRIMARY_SOURCE_DOCUMENT below. This is what makes "does this rendered
document contain exactly the fields it's supposed to" a checkable property rather than an
eyeballed one (see test_document_templates.py).

**Academic record is the one deliberate exception.** A degree certificate naturally repeats
the holder's name and date of birth -- it corroborates the ID proof's own full_name and
date_of_birth rather than sourcing a field of its own. It introduces no field that isn't
already a real form-schema field (test_document_templates.py checks this against the
committed form schemas directly, not just this module's own claim).

Two fields the identity generator produces -- Identity.city and Identity.state -- are never
rendered directly by any template here. They exist only to build residential_address's
prose and to make Derive's ground truth checkable (Commit 1); neither is a form-schema
field, so neither needs a document to source it.

Nominee identity (insurance-only) is deliberately out of scope for this module: it needs a
*second* identity, which is a case-assembly decision, not a template-rendering one -- left
to Commit 3.

Lives entirely in eval/ -- this is synthetic document *text* generation for the evaluation
dataset, not a real ingestion path. Invariant P1 has nothing to say about it, and no
production code under app/ is imported or modified here.
"""

from collections.abc import Callable

from eval.dataset.identity_generator import Identity

ID_PROOF = "id_proof"
ADDRESS_PROOF = "address_proof"
INCOME_DOCUMENT = "income_document"
ACADEMIC_RECORD = "academic_record"

DOCUMENT_TYPES: tuple[str, ...] = (ID_PROOF, ADDRESS_PROOF, INCOME_DOCUMENT, ACADEMIC_RECORD)

# Every field a given document type's rendered text embeds. ACADEMIC_RECORD's entry
# overlaps ID_PROOF's by design (corroboration) -- every other pair is disjoint.
DOCUMENT_FIELD_SETS: dict[str, frozenset[str]] = {
    ID_PROOF: frozenset(
        {"full_name", "date_of_birth", "pan_number", "aadhaar_number", "phone_number", "email_address"}
    ),
    ADDRESS_PROOF: frozenset({"residential_address", "pin_code"}),
    INCOME_DOCUMENT: frozenset({"annual_income", "linked_account_number"}),
    ACADEMIC_RECORD: frozenset({"full_name", "date_of_birth"}),
}

# The one document each field should be treated as *sourced from* when only one evidence
# document may be cited (e.g. provenance display) -- academic_record is deliberately absent
# here even though it also carries full_name/date_of_birth, since id_proof is the primary.
PRIMARY_SOURCE_DOCUMENT: dict[str, str] = {
    "full_name": ID_PROOF,
    "date_of_birth": ID_PROOF,
    "pan_number": ID_PROOF,
    "aadhaar_number": ID_PROOF,
    "phone_number": ID_PROOF,
    "email_address": ID_PROOF,
    "residential_address": ADDRESS_PROOF,
    "pin_code": ADDRESS_PROOF,
    "annual_income": INCOME_DOCUMENT,
    "linked_account_number": INCOME_DOCUMENT,
}


def _format_dob(identity: Identity) -> str:
    return identity.date_of_birth.strftime("%d/%m/%Y")


def render_id_proof(identity: Identity) -> str:
    return (
        "GOVERNMENT OF INDIA\n"
        "IDENTITY PROOF DOCUMENT\n\n"
        f"Full Name: {identity.full_name}\n"
        f"Date of Birth: {_format_dob(identity)}\n"
        f"PAN Number: {identity.pan_number}\n"
        f"Aadhaar Number: {identity.aadhaar_number}\n"
        f"Phone Number: {identity.phone_number}\n"
        f"Email Address: {identity.email_address}\n\n"
        "This document certifies the identity of the above named individual for the "
        "purpose of account opening and KYC verification."
    )


def render_address_proof(identity: Identity) -> str:
    return (
        "ELECTRICITY BILL - ADDRESS PROOF\n\n"
        f"Address: {identity.residential_address}\n"
        f"PIN Code: {identity.pin_code}\n\n"
        "Billing Period: June 2026"
    )


def render_income_document(identity: Identity) -> str:
    return (
        "SALARY CERTIFICATE\n\n"
        f"Annual Income: {identity.annual_income}\n"
        f"Bank Account Number: {identity.linked_account_number}\n\n"
        "This certifies that the above named individual draws the stated annual income "
        "and holds the stated bank account, as of the current financial year."
    )


def render_academic_record(identity: Identity) -> str:
    return (
        "DEGREE CERTIFICATE\n\n"
        f"This is to certify that {identity.full_name}, born {_format_dob(identity)}, "
        "has been awarded the degree of Bachelor of Science by this university, having "
        "satisfactorily completed the prescribed course of study and all examinations "
        "connected therewith."
    )


RENDERERS: dict[str, Callable[[Identity], str]] = {
    ID_PROOF: render_id_proof,
    ADDRESS_PROOF: render_address_proof,
    INCOME_DOCUMENT: render_income_document,
    ACADEMIC_RECORD: render_academic_record,
}


def render_document(document_type: str, identity: Identity) -> str:
    return RENDERERS[document_type](identity)
