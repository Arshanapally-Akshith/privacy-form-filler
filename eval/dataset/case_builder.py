"""Case assembler and adversarial transforms for the Phase 6 evaluation dataset (BUILD.md
Phase 6 tasks 4-5, V4/V5).

A Case combines one primary Identity (Commit 1), a set of rendered documents (Commit 2),
and the ground truth every field should extract to. Five builders live here: one clean
(non-adversarial) baseline and four adversarial transforms, one per DECISIONS.md V5 type
(conflicting values, missing required field, near-duplicate names, unusual formats).

**Transforms modify identities and document composition, never rendered text.** Every
builder decides (a) which Identity/Identities feed which document, and (b) which document
*types* are included in the case -- then calls eval.dataset.document_templates.render_document
(or, for the two cases that genuinely need a document Commit 2 doesn't have, a small local
renderer of the same pure-function-of-one-identity shape). No builder ever takes an already
-rendered string and edits it. This is what makes ground truth computable purely from
"which document types are present and which identity rendered them" (_compute_ground_truth
below), rather than needing to re-parse text to know what a transform did.

**Conflict reuses Commit 2's one deliberate overlap.** document_templates.DOCUMENT_FIELD_SETS
already has exactly one pair of documents sharing fields -- id_proof and academic_record,
on full_name/date_of_birth. build_conflicting_case renders id_proof from the primary
identity and academic_record from a second identity that differs from the primary in
exactly that one field, so the two documents genuinely disagree. Ground truth records the
*primary* (canonical) value plus an explicit adversarial tag -- never null -- per the
approved Commit 3 decision: a conflict is a resolvable ambiguity (the verifier is expected
to escalate it to human review, ARCHITECTURE.md §7), not a genuine absence.

**Nominee identity is scoped to insurance cases only**, and lives entirely in this module.
document_templates.py's four renderers stay single-identity-pure and untouched --
nominee_full_name is carried by a fifth, case-builder-local "nominee declaration" text
block, itself a pure function of one (nominee) identity, not a parameter bolted onto an
existing renderer.

**Unusual-format cases** reformat a value at the identity level, not the text level: the
alternate id_proof renderer below calls identity.date_of_birth.strftime() with a different
(but genuinely supported -- see app.privacy.generalize.DATE_OF_BIRTH_FORMATS) format
string, or prepends "+91-" to the identity's own phone digits. Ground truth always records
the canonical form, so "does the pipeline recover the same value from a differently
-formatted source" is exactly what these cases test.

Lives entirely in eval/ -- no file under app/ is imported for anything other than read-only
reference (FormSchema for typing, DATE_OF_BIRTH_FORMATS-compatible formatting) and none is
modified.
"""

import random
import string
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date

from app.config.form_schema import FormSchema
from eval.dataset.document_templates import (
    ACADEMIC_RECORD,
    ADDRESS_PROOF,
    DOCUMENT_FIELD_SETS,
    ID_PROOF,
    INCOME_DOCUMENT,
    render_document,
)
from eval.dataset.identity_generator import Identity, generate_identity

NOMINEE_DECLARATION = "nominee_declaration"
NOMINEE_FIELD_NAME = "nominee_full_name"

CONFLICT_FIELDS: tuple[str, ...] = ("full_name", "date_of_birth")
UNUSUAL_FORMAT_FIELDS: tuple[str, ...] = ("date_of_birth", "phone_number")
# Fields that removing a document type is allowed to null out, restricted to the two
# document types with no corroborating backup elsewhere (id_proof's full_name/date_of_birth
# are also carried by academic_record, so omitting id_proof alone would not actually make
# them absent -- see the module docstring's "never mutate rendered text" note, which applies
# here too: composition, not text-surgery, decides what's missing).
OMITTABLE_DOCUMENT_TYPES: tuple[str, ...] = (ADDRESS_PROOF, INCOME_DOCUMENT)

_CANONICAL_DOB_FORMAT = "%d/%m/%Y"
_ALTERNATE_DOB_FORMAT = "%d %B %Y"  # also in app.privacy.generalize.DATE_OF_BIRTH_FORMATS


@dataclass(frozen=True)
class CaseDocument:
    document_id: str
    document_type: str
    text: str


@dataclass(frozen=True)
class AdversarialInfo:
    adversarial_type: str
    detail: dict[str, str]


@dataclass(frozen=True)
class Case:
    case_id: str
    form_schema_id: str
    documents: tuple[CaseDocument, ...]
    ground_truth: dict[str, str | None]
    adversarial: AdversarialInfo | None
    reference_date: date
    primary_identity: Identity
    # Any identity other than the primary that contributed a document to this case --
    # the conflicting variant, the near-duplicate, and/or the nominee. Exposed (rather than
    # left only inside rendered text) so tests, and Commit 6's dataset-wide consistency
    # check, can assert against structured values instead of re-parsing prose.
    secondary_identities: tuple[Identity, ...] = ()


def _document(case_id: str, document_type: str, identity: Identity) -> CaseDocument:
    return CaseDocument(
        document_id=f"{case_id}__{document_type}",
        document_type=document_type,
        text=render_document(document_type, identity),
    )


def _ground_truth_value(identity: Identity, field_name: str) -> str:
    if field_name == "date_of_birth":
        return identity.date_of_birth.strftime(_CANONICAL_DOB_FORMAT)
    return str(getattr(identity, field_name))


def _schema_needs_nominee(schema: FormSchema) -> bool:
    return any(field.name == NOMINEE_FIELD_NAME for field in schema.fields)


def _render_nominee_declaration(nominee: Identity) -> str:
    return (
        "NOMINEE DECLARATION\n\n"
        f"Nominee Full Name: {nominee.full_name}\n"
        "Relationship to Policy Holder: Spouse\n\n"
        "The above named individual is declared as nominee for this insurance policy."
    )


def _nominee_documents_and_identity(
    rng: random.Random, schema: FormSchema, reference_date: date, case_id: str
) -> tuple[list[CaseDocument], Identity | None]:
    if not _schema_needs_nominee(schema):
        return [], None
    nominee_identity = generate_identity(rng, reference_date)
    document = CaseDocument(
        document_id=f"{case_id}__{NOMINEE_DECLARATION}",
        document_type=NOMINEE_DECLARATION,
        text=_render_nominee_declaration(nominee_identity),
    )
    return [document], nominee_identity


def _compute_ground_truth(
    identity: Identity,
    schema: FormSchema,
    included_document_types: Iterable[str],
    nominee_identity: Identity | None,
) -> dict[str, str | None]:
    covered_fields: set[str] = set()
    for document_type in included_document_types:
        covered_fields |= DOCUMENT_FIELD_SETS[document_type]

    ground_truth: dict[str, str | None] = {}
    for field in schema.fields:
        if field.name == NOMINEE_FIELD_NAME:
            ground_truth[field.name] = nominee_identity.full_name if nominee_identity is not None else None
            continue
        ground_truth[field.name] = _ground_truth_value(identity, field.name) if field.name in covered_fields else None
    return ground_truth


def build_clean_case(rng: random.Random, case_id: str, schema: FormSchema, reference_date: date) -> Case:
    """The non-adversarial baseline: every field genuinely evidenced, every document
    consistent with the one primary identity (plus a nominee identity for insurance)."""
    identity = generate_identity(rng, reference_date)
    document_types = [ID_PROOF, ADDRESS_PROOF, INCOME_DOCUMENT, ACADEMIC_RECORD]
    documents = [_document(case_id, document_type, identity) for document_type in document_types]

    nominee_documents, nominee_identity = _nominee_documents_and_identity(rng, schema, reference_date, case_id)
    documents.extend(nominee_documents)

    ground_truth = _compute_ground_truth(identity, schema, document_types, nominee_identity)
    return Case(
        case_id=case_id,
        form_schema_id=schema.id,
        documents=tuple(documents),
        ground_truth=ground_truth,
        adversarial=None,
        reference_date=reference_date,
        primary_identity=identity,
        secondary_identities=((nominee_identity,) if nominee_identity is not None else ()),
    )


def _distinct_full_name(rng: random.Random, reference_date: date, exclude: str) -> str:
    for _ in range(10):
        candidate = generate_identity(rng, reference_date).full_name
        if candidate != exclude:
            return candidate
    raise RuntimeError("Could not draw a full name distinct from the primary identity's after 10 attempts")


def build_conflicting_case(
    rng: random.Random,
    case_id: str,
    schema: FormSchema,
    reference_date: date,
    *,
    field: str | None = None,
) -> Case:
    """id_proof and academic_record -- the one pair of documents Commit 2 deliberately
    lets overlap -- are rendered from two identities that disagree on exactly `field`.
    Ground truth is the primary (canonical) value, tagged adversarial_type="conflict";
    never null (approved Commit 3 decision)."""
    target_field = field if field is not None else rng.choice(CONFLICT_FIELDS)
    if target_field not in CONFLICT_FIELDS:
        raise ValueError(f"Unsupported conflict field: {target_field!r}. Supported: {CONFLICT_FIELDS}")

    identity = generate_identity(rng, reference_date)
    if target_field == "date_of_birth":
        conflicting_dob = identity.date_of_birth.replace(year=identity.date_of_birth.year - 5)
        conflicting_identity = replace(identity, date_of_birth=conflicting_dob)
    else:
        conflicting_name = _distinct_full_name(rng, reference_date, exclude=identity.full_name)
        conflicting_identity = replace(identity, full_name=conflicting_name)

    document_types = [ID_PROOF, ADDRESS_PROOF, INCOME_DOCUMENT, ACADEMIC_RECORD]
    documents = [
        _document(case_id, ID_PROOF, identity),
        _document(case_id, ADDRESS_PROOF, identity),
        _document(case_id, INCOME_DOCUMENT, identity),
        _document(case_id, ACADEMIC_RECORD, conflicting_identity),
    ]

    nominee_documents, nominee_identity = _nominee_documents_and_identity(rng, schema, reference_date, case_id)
    documents.extend(nominee_documents)

    ground_truth = _compute_ground_truth(identity, schema, document_types, nominee_identity)
    secondary = (conflicting_identity, *((nominee_identity,) if nominee_identity is not None else ()))

    detail = {
        "field": target_field,
        "primary_value": _ground_truth_value(identity, target_field),
        "conflicting_value": _ground_truth_value(conflicting_identity, target_field),
        "primary_document_type": ID_PROOF,
        "conflicting_document_type": ACADEMIC_RECORD,
    }
    return Case(
        case_id=case_id,
        form_schema_id=schema.id,
        documents=tuple(documents),
        ground_truth=ground_truth,
        adversarial=AdversarialInfo("conflict", detail),
        reference_date=reference_date,
        primary_identity=identity,
        secondary_identities=secondary,
    )


def build_missing_field_case(
    rng: random.Random,
    case_id: str,
    schema: FormSchema,
    reference_date: date,
    *,
    omitted_document_type: str,
) -> Case:
    """Excludes `omitted_document_type` from the case's document set entirely -- no
    document anywhere mentions the fields it would have carried. Fails loudly if that
    doesn't actually null out a *required* field for this schema (CLAUDE.md §5): a
    "missing field" case that only omits an optional field isn't the adversarial type
    DECISIONS.md V5 names."""
    if omitted_document_type not in OMITTABLE_DOCUMENT_TYPES:
        raise ValueError(
            f"Unsupported omitted_document_type: {omitted_document_type!r}. "
            f"Supported: {OMITTABLE_DOCUMENT_TYPES}"
        )

    identity = generate_identity(rng, reference_date)
    document_types = [dt for dt in (ID_PROOF, ADDRESS_PROOF, INCOME_DOCUMENT, ACADEMIC_RECORD) if dt != omitted_document_type]
    documents = [_document(case_id, document_type, identity) for document_type in document_types]

    nominee_documents, nominee_identity = _nominee_documents_and_identity(rng, schema, reference_date, case_id)
    documents.extend(nominee_documents)

    ground_truth = _compute_ground_truth(identity, schema, document_types, nominee_identity)

    schema_field_names = {f.name for f in schema.fields}
    omitted_fields = sorted(DOCUMENT_FIELD_SETS[omitted_document_type] & schema_field_names)
    omitted_required_fields = sorted(f.name for f in schema.fields if f.name in omitted_fields and f.required)
    if not omitted_required_fields:
        raise ValueError(
            f"Omitting {omitted_document_type!r} from schema {schema.id!r} does not remove any "
            "required field -- not a valid missing-required-field adversarial case for this schema"
        )

    detail = {
        "omitted_document_type": omitted_document_type,
        "omitted_fields": ",".join(omitted_fields),
        "omitted_required_fields": ",".join(omitted_required_fields),
    }
    return Case(
        case_id=case_id,
        form_schema_id=schema.id,
        documents=tuple(documents),
        ground_truth=ground_truth,
        adversarial=AdversarialInfo("missing_field", detail),
        reference_date=reference_date,
        primary_identity=identity,
        secondary_identities=((nominee_identity,) if nominee_identity is not None else ()),
    )


def _minor_spelling_variant(rng: random.Random, word: str) -> str:
    if len(word) < 2:
        return word + rng.choice(string.ascii_lowercase)
    index = len(word) - 1
    original = word[index].lower()
    replacement = rng.choice([c for c in string.ascii_lowercase if c != original])
    return word[:index] + replacement


def _near_duplicate_name(rng: random.Random, full_name: str) -> str:
    """A one-character spelling variant of the first name, same last name -- a realistic
    typo/transcription near-duplicate ("Priya Sharma" vs "Priyx Sharma"), not an unrelated
    person who happens to share a surname."""
    first_name, _, last_name = full_name.partition(" ")
    return f"{_minor_spelling_variant(rng, first_name)} {last_name}"


def build_near_duplicate_name_case(rng: random.Random, case_id: str, schema: FormSchema, reference_date: date) -> Case:
    """The primary applicant's own academic_record is replaced by one belonging to a
    second, similarly-named person -- a realistic document-collection mixup. Ground truth
    is entirely the primary identity's; the duplicate's document is a distractor, not a
    source for any field."""
    identity = generate_identity(rng, reference_date)
    duplicate_seed_identity = generate_identity(rng, reference_date)
    duplicate_identity = replace(duplicate_seed_identity, full_name=_near_duplicate_name(rng, identity.full_name))

    document_types = [ID_PROOF, ADDRESS_PROOF, INCOME_DOCUMENT]  # academic_record's slot goes to the duplicate below
    documents = [_document(case_id, document_type, identity) for document_type in document_types]
    documents.append(
        CaseDocument(
            document_id=f"{case_id}__{ACADEMIC_RECORD}_duplicate",
            document_type=ACADEMIC_RECORD,
            text=render_document(ACADEMIC_RECORD, duplicate_identity),
        )
    )

    nominee_documents, nominee_identity = _nominee_documents_and_identity(rng, schema, reference_date, case_id)
    documents.extend(nominee_documents)

    ground_truth = _compute_ground_truth(identity, schema, document_types, nominee_identity)
    secondary = (duplicate_identity, *((nominee_identity,) if nominee_identity is not None else ()))

    detail = {
        "primary_name": identity.full_name,
        "duplicate_name": duplicate_identity.full_name,
        "duplicate_document_type": ACADEMIC_RECORD,
    }
    return Case(
        case_id=case_id,
        form_schema_id=schema.id,
        documents=tuple(documents),
        ground_truth=ground_truth,
        adversarial=AdversarialInfo("near_duplicate_name", detail),
        reference_date=reference_date,
        primary_identity=identity,
        secondary_identities=secondary,
    )


def _render_id_proof_variant(identity: Identity, *, dob_format: str = _CANONICAL_DOB_FORMAT, phone_with_country_code: bool = False) -> str:
    phone_text = f"+91-{identity.phone_number}" if phone_with_country_code else identity.phone_number
    return (
        "GOVERNMENT OF INDIA\n"
        "IDENTITY PROOF DOCUMENT\n\n"
        f"Full Name: {identity.full_name}\n"
        f"Date of Birth: {identity.date_of_birth.strftime(dob_format)}\n"
        f"PAN Number: {identity.pan_number}\n"
        f"Aadhaar Number: {identity.aadhaar_number}\n"
        f"Phone Number: {phone_text}\n"
        f"Email Address: {identity.email_address}\n\n"
        "This document certifies the identity of the above named individual for the "
        "purpose of account opening and KYC verification."
    )


def build_unusual_format_case(
    rng: random.Random,
    case_id: str,
    schema: FormSchema,
    reference_date: date,
    *,
    field: str | None = None,
) -> Case:
    """Reformats exactly one field's value at the identity level -- an alternate but
    genuinely-supported date format, or a +91-prefixed phone number -- while ground truth
    keeps recording the canonical form, so "does extraction still recover the canonical
    value from unusual-but-valid input formatting" is what this case tests."""
    target_field = field if field is not None else rng.choice(UNUSUAL_FORMAT_FIELDS)
    if target_field not in UNUSUAL_FORMAT_FIELDS:
        raise ValueError(f"Unsupported unusual-format field: {target_field!r}. Supported: {UNUSUAL_FORMAT_FIELDS}")

    identity = generate_identity(rng, reference_date)
    if target_field == "date_of_birth":
        id_proof_text = _render_id_proof_variant(identity, dob_format=_ALTERNATE_DOB_FORMAT)
        document_format_value = identity.date_of_birth.strftime(_ALTERNATE_DOB_FORMAT)
    else:
        id_proof_text = _render_id_proof_variant(identity, phone_with_country_code=True)
        document_format_value = f"+91-{identity.phone_number}"

    document_types = [ID_PROOF, ADDRESS_PROOF, INCOME_DOCUMENT, ACADEMIC_RECORD]
    documents = [
        CaseDocument(document_id=f"{case_id}__{ID_PROOF}", document_type=ID_PROOF, text=id_proof_text),
        _document(case_id, ADDRESS_PROOF, identity),
        _document(case_id, INCOME_DOCUMENT, identity),
        _document(case_id, ACADEMIC_RECORD, identity),
    ]

    nominee_documents, nominee_identity = _nominee_documents_and_identity(rng, schema, reference_date, case_id)
    documents.extend(nominee_documents)

    ground_truth = _compute_ground_truth(identity, schema, document_types, nominee_identity)
    canonical_value = ground_truth[target_field]
    assert canonical_value is not None  # both UNUSUAL_FORMAT_FIELDS are always id_proof-covered

    detail = {
        "field": target_field,
        "document_format_value": document_format_value,
        "canonical_ground_truth_value": canonical_value,
    }
    return Case(
        case_id=case_id,
        form_schema_id=schema.id,
        documents=tuple(documents),
        ground_truth=ground_truth,
        adversarial=AdversarialInfo("unusual_format", detail),
        reference_date=reference_date,
        primary_identity=identity,
        secondary_identities=((nominee_identity,) if nominee_identity is not None else ()),
    )
