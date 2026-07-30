"""Full Phase 6 evaluation dataset build (BUILD.md Phase 6 deliverable: "50-60
semi-synthetic cases with ground-truth labels"). Assembles every case from Commit 1-4's
building blocks -- eval.dataset.identity_generator, eval.dataset.document_templates (via
case_builder), eval.dataset.case_builder itself, and the OCR-representative subset already
committed by Commit 4 -- and writes the result as a single committed JSON artifact,
following the same "commit the generated output, don't regenerate at test time" precedent
as eval/dataset/generate_phase1_fixtures.py and eval/dataset/generate_ocr_subset.py.
test_phase6_dataset.py validates the committed file directly; it never calls
generate_dataset() except in the one test that specifically checks the committed file still
matches what the generator currently produces.

**Composition** (DECISIONS.md V3/V4/V5/V6, BUILD.md Phase 6 exit criteria): 56 cases total,
28 per form schema. Per schema: 1 clean case reused verbatim from Commit 4's
OCR-representative subset (case_id "phase6_ocr_kyc" / "phase6_ocr_insurance" -- the exact
case whose documents are also rendered as images in fixtures/phase6_ocr/, built by calling
eval.dataset.generate_ocr_subset.build_representative_cases() rather than re-deriving it,
so the two are guaranteed identical rather than merely intended to match), 21 additional
clean cases, and 6 adversarial cases covering all four DECISIONS.md V5 types (conflict,
missing_field, near_duplicate_name, unusual_format). 12 adversarial cases overall (21% of
56, "roughly 20%" per V4).

**OCR coverage, without duplicating the mapping.** fixtures/phase6_ocr/manifest.json is the
sole authoritative record of which document became which rendered image, under which
condition (DECISIONS.md V17/V18) -- Commit 4's own docstring already says so. This file
does not repeat any of that: it records only ocr_manifest_path (a pointer), never image
filenames, conditions, or which case_ids the manifest covers. A consumer wanting to know
"does this case have an OCR rendering" reads the manifest's own source_case_id field
directly -- test_phase6_dataset.py does exactly that, rather than trusting a second,
hand-maintained list here that could drift from the manifest's actual contents.

**Metadata.** dataset_version, identity_generator_version (mirrored from
eval.dataset.identity_generator.IDENTITY_GENERATOR_VERSION so the two cannot silently
diverge -- test_phase6_dataset.py checks this), generator_seed, and generator_commit (the
repository's HEAD commit at generation time, best-effort -- "unknown" if git metadata isn't
available, which is an honest provenance state, not a masked failure of anything
correctness-relevant). None of this is asked for by BUILD.md itself; it is what makes "does
this committed dataset still match what the generator would produce today" answerable
without re-deriving anything by hand.
"""

import hashlib
import json
import random
import subprocess
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path

from app.config.form_schema import FormSchema, load_form_schemas
from eval.dataset.case_builder import (
    ADDRESS_PROOF,
    INCOME_DOCUMENT,
    Case,
    build_clean_case,
    build_conflicting_case,
    build_missing_field_case,
    build_near_duplicate_name_case,
    build_unusual_format_case,
)
from eval.dataset.generate_ocr_subset import REFERENCE_DATE, build_representative_cases
from eval.dataset.identity_generator import IDENTITY_GENERATOR_VERSION

DATASET_DIR = Path(__file__).resolve().parent
DATASET_PATH = DATASET_DIR / "phase6_eval_cases.json"
OCR_MANIFEST_RELATIVE_PATH = "fixtures/phase6_ocr/manifest.json"

DATASET_VERSION = "1.0.0"
GENERATOR_SEED = 60000

KYC_SCHEMA_ID = "kyc_account_opening"
INSURANCE_SCHEMA_ID = "insurance_policy_application"

CLEAN_EXTRA_PER_SCHEMA = 21


def _seed_for(case_id: str) -> int:
    # Same pattern eval.dataset.generate_ocr_subset._photo_rng_for already uses: an explicit
    # hash of a stable string, not a shared rng stream advanced case-by-case, so adding,
    # removing, or reordering cases later cannot shift any *other* case's identity.
    digest = hashlib.sha256(f"{GENERATOR_SEED}:{case_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _rng_for(case_id: str) -> random.Random:
    return random.Random(_seed_for(case_id))


def _build_schema_cases(
    schema: FormSchema,
    prefix: str,
    *,
    clean_count: int,
    conflict_fields: list[str],
    missing_field_document_types: list[str],
    near_duplicate_count: int,
    unusual_format_fields: list[str],
) -> list[Case]:
    cases: list[Case] = []

    for i in range(1, clean_count + 1):
        case_id = f"{prefix}_clean_{i:03d}"
        cases.append(build_clean_case(_rng_for(case_id), case_id, schema, REFERENCE_DATE))

    for i, field in enumerate(conflict_fields, start=1):
        case_id = f"{prefix}_conflict_{i:03d}"
        cases.append(build_conflicting_case(_rng_for(case_id), case_id, schema, REFERENCE_DATE, field=field))

    for i, document_type in enumerate(missing_field_document_types, start=1):
        case_id = f"{prefix}_missing_field_{i:03d}"
        cases.append(
            build_missing_field_case(
                _rng_for(case_id), case_id, schema, REFERENCE_DATE, omitted_document_type=document_type
            )
        )

    for i in range(1, near_duplicate_count + 1):
        case_id = f"{prefix}_near_duplicate_{i:03d}"
        cases.append(build_near_duplicate_name_case(_rng_for(case_id), case_id, schema, REFERENCE_DATE))

    for i, field in enumerate(unusual_format_fields, start=1):
        case_id = f"{prefix}_unusual_format_{i:03d}"
        cases.append(build_unusual_format_case(_rng_for(case_id), case_id, schema, REFERENCE_DATE, field=field))

    return cases


def build_dataset_cases() -> list[Case]:
    schemas = {schema.id: schema for schema in load_form_schemas()}
    kyc_schema = schemas[KYC_SCHEMA_ID]
    insurance_schema = schemas[INSURANCE_SCHEMA_ID]

    ocr_kyc_case, ocr_insurance_case = build_representative_cases()

    kyc_cases = _build_schema_cases(
        kyc_schema,
        "kyc",
        clean_count=CLEAN_EXTRA_PER_SCHEMA,
        conflict_fields=["date_of_birth", "full_name"],
        missing_field_document_types=[ADDRESS_PROOF],
        near_duplicate_count=2,
        unusual_format_fields=["date_of_birth"],
    )
    insurance_cases = _build_schema_cases(
        insurance_schema,
        "insurance",
        clean_count=CLEAN_EXTRA_PER_SCHEMA,
        conflict_fields=["date_of_birth"],
        missing_field_document_types=[ADDRESS_PROOF, INCOME_DOCUMENT],
        near_duplicate_count=1,
        unusual_format_fields=["date_of_birth", "phone_number"],
    )

    return [ocr_kyc_case, *kyc_cases, ocr_insurance_case, *insurance_cases]


def _generator_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=DATASET_DIR,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return "unknown"


def build_metadata(cases: list[Case]) -> dict[str, object]:
    return {
        "dataset_version": DATASET_VERSION,
        "identity_generator_version": IDENTITY_GENERATOR_VERSION,
        "generator_seed": GENERATOR_SEED,
        "generator_commit": _generator_commit(),
        "generated_at": datetime.now(UTC).isoformat(),
        "reference_date": REFERENCE_DATE.isoformat(),
        "case_count": len(cases),
        "ocr_manifest_path": OCR_MANIFEST_RELATIVE_PATH,
    }


def _serialize_case(case: Case) -> dict[str, object]:
    return {
        "case_id": case.case_id,
        "form_schema_id": case.form_schema_id,
        "reference_date": case.reference_date.isoformat(),
        "documents": [asdict(document) for document in case.documents],
        "ground_truth": case.ground_truth,
        "adversarial": asdict(case.adversarial) if case.adversarial is not None else None,
        "primary_identity": asdict(case.primary_identity),
        "secondary_identities": [asdict(identity) for identity in case.secondary_identities],
    }


def generate_dataset() -> dict[str, object]:
    cases = build_dataset_cases()
    return {
        "metadata": build_metadata(cases),
        "cases": [_serialize_case(case) for case in cases],
    }


def _json_default(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value)} is not JSON serializable")


def main() -> None:
    dataset = generate_dataset()
    DATASET_PATH.write_text(
        json.dumps(dataset, indent=2, sort_keys=True, default=_json_default) + "\n", encoding="utf-8"
    )
    print(f"Wrote {len(dataset['cases'])} cases to {DATASET_PATH}")  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
