"""Ground-truth internal consistency tests for the committed Phase 6 evaluation dataset
(BUILD.md Phase 6 "written alongside" requirement: "Generated cases: ground truth is
internally consistent across a case's documents").

**Deliberately independent of the dataset generator.** This file reads
eval/dataset/phase6_eval_cases.json (the committed artifact) with plain json.loads and its
own hardcoded domain constants below -- it does not import eval.dataset.case_builder or
eval.dataset.document_templates. If it instead imported and reused case_builder's own
DOCUMENT_FIELD_SETS / PRIMARY_SOURCE_DOCUMENT mapping to decide what "should" be
consistent, a bug in that mapping would validate itself: the generator and the validator
would share the exact same wrong assumption, and this file could never catch it. The only
knowledge shared with the generator here is stable, documented, higher-level design
(ARCHITECTURE.md D13's shared four-document-type pool; document_templates.py's own
docstring naming id_proof/academic_record as the one deliberate overlap on
full_name/date_of_birth) and one pinned, project-wide constant
(app.privacy.constants.DATE_OF_BIRTH_FORMATS) -- never case_builder's implementation.

**What "internally consistent" means here.** id_proof and academic_record are the only two
document types in this dataset that both carry the same fields (full_name, date_of_birth).
Wherever both are present in a case (every case, in this dataset), each document's text
must contain a value traceable to a *recorded* identity on that case --
case["primary_identity"], or one of case["secondary_identities"] -- never an arbitrary,
unexplained string. A recorded secondary identity is exactly what a legitimate
near-duplicate-name case's substitute document, or a conflict case's deliberately-differing
document, looks like; a value that matches *no* recorded identity would be an unexplained
mismatch -- a real generator bug, which is what this file is actually trying to catch.

**Date-of-birth format tolerance.** A document may render date_of_birth in any of
app.privacy.constants.DATE_OF_BIRTH_FORMATS -- the full pinned list the real pipeline's own
parser (app.privacy.generalize.parse_date_of_birth) accepts, not just the one canonical
format case_builder happens to default to. This is what lets an unusual_format-tagged
case's alternately-formatted id_proof pass without this file needing to know that
transform, or its exact alternate format string, exists at all.

**Conflict cases** are excluded from the generic corroboration check above -- a conflict
case's two documents are *supposed* to disagree; that disagreement is the entire point of
the adversarial type, not a defect. They get their own dedicated check instead, verified
against their own recorded adversarial.detail: the primary document must show the case's
own canonical ground-truth value, and the conflicting document must show the documented
conflicting_value -- an active verification against documented semantics, not a silent skip.
"""

import json
from datetime import date
from typing import Any

from app.privacy.constants import DATE_OF_BIRTH_FORMATS
from eval.dataset.generate_phase6_dataset import DATASET_PATH

_CORROBORATED_FIELDS = ("full_name", "date_of_birth")
_PRIMARY_CORROBORATING_DOCUMENT_TYPE = "id_proof"
_SECONDARY_CORROBORATING_DOCUMENT_TYPE = "academic_record"


def _load_committed_dataset() -> dict[str, Any]:
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def _acceptable_values(identities: list[dict[str, Any]], field: str) -> set[str]:
    """Every textual representation a document could legitimately show for `field`, across
    every identity recorded on the case -- the primary applicant, plus any secondary
    identity a conflict/near-duplicate transform introduced."""
    values: set[str] = set()
    for identity in identities:
        if field == "date_of_birth":
            dob = date.fromisoformat(identity["date_of_birth"])
            values.update(dob.strftime(fmt) for fmt in DATE_OF_BIRTH_FORMATS)
        else:
            values.add(identity[field])
    return values


def _document(case: dict[str, Any], document_type: str) -> dict[str, Any]:
    matches = [d for d in case["documents"] if d["document_type"] == document_type]
    assert matches, (
        f"case {case['case_id']!r} has no document of type {document_type!r} -- every case "
        "in this dataset is expected to carry both id_proof and academic_record"
    )
    assert len(matches) == 1, (
        f"case {case['case_id']!r} has {len(matches)} documents of type {document_type!r}, expected exactly 1"
    )
    return matches[0]


def _is_conflict_case(case: dict[str, Any]) -> bool:
    return case["adversarial"] is not None and case["adversarial"]["adversarial_type"] == "conflict"


def test_every_non_conflict_case_corroborates_full_name_and_dob_consistently() -> None:
    dataset = _load_committed_dataset()
    non_conflict_cases = [case for case in dataset["cases"] if not _is_conflict_case(case)]
    assert non_conflict_cases  # sanity: the loop below would vacuously pass on an empty list

    for case in non_conflict_cases:
        identities = [case["primary_identity"], *case["secondary_identities"]]
        id_proof = _document(case, _PRIMARY_CORROBORATING_DOCUMENT_TYPE)
        academic_record = _document(case, _SECONDARY_CORROBORATING_DOCUMENT_TYPE)

        for field in _CORROBORATED_FIELDS:
            acceptable = _acceptable_values(identities, field)
            for document in (id_proof, academic_record):
                assert any(value in document["text"] for value in acceptable), (
                    f"case {case['case_id']!r}: document {document['document_id']!r} "
                    f"(type {document['document_type']!r}) does not contain any recorded identity's "
                    f"value for field {field!r}.\n"
                    f"Acceptable values (from primary_identity + secondary_identities): {sorted(acceptable)!r}\n"
                    f"Document text:\n{document['text']}"
                )


def test_every_conflict_case_matches_its_own_documented_semantics() -> None:
    dataset = _load_committed_dataset()
    conflict_cases = [case for case in dataset["cases"] if _is_conflict_case(case)]
    assert conflict_cases  # sanity: the loop below would vacuously pass on an empty list

    for case in conflict_cases:
        detail = case["adversarial"]["detail"]
        field = detail["field"]
        primary_value = detail["primary_value"]
        conflicting_value = detail["conflicting_value"]

        assert primary_value != conflicting_value, (
            f"case {case['case_id']!r}: conflict on field {field!r} has identical primary_value "
            f"and conflicting_value ({primary_value!r}) -- not a genuine conflict"
        )
        assert case["ground_truth"][field] == primary_value, (
            f"case {case['case_id']!r}: ground_truth[{field!r}] is {case['ground_truth'][field]!r}, "
            f"expected it to equal the documented primary_value {primary_value!r}"
        )

        primary_document = _document(case, detail["primary_document_type"])
        conflicting_document = _document(case, detail["conflicting_document_type"])

        assert primary_value in primary_document["text"], (
            f"case {case['case_id']!r}, field {field!r}: primary document "
            f"{primary_document['document_id']!r} does not contain the documented "
            f"primary_value {primary_value!r}\nDocument text:\n{primary_document['text']}"
        )
        assert conflicting_value in conflicting_document["text"], (
            f"case {case['case_id']!r}, field {field!r}: conflicting document "
            f"{conflicting_document['document_id']!r} does not contain the documented "
            f"conflicting_value {conflicting_value!r}\nDocument text:\n{conflicting_document['text']}"
        )
        assert conflicting_value not in primary_document["text"], (
            f"case {case['case_id']!r}, field {field!r}: primary document "
            f"{primary_document['document_id']!r} unexpectedly also contains the "
            f"conflicting_value {conflicting_value!r} -- the two documents should disagree, not both show it"
        )
        assert primary_value not in conflicting_document["text"], (
            f"case {case['case_id']!r}, field {field!r}: conflicting document "
            f"{conflicting_document['document_id']!r} unexpectedly also contains the "
            f"primary_value {primary_value!r} -- the two documents should disagree, not both show it"
        )
