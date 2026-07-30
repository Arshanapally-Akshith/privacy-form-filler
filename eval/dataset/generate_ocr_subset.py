"""One-time generator for the Phase 6 representative OCR-evaluation image subset
(BUILD.md Phase 6 task 3a, DECISIONS.md V17). Mirrors
eval/dataset/generate_phase1_fixtures.py's own precedent: the committed images and
manifest.json in fixtures/phase6_ocr/ ARE the evaluation fixtures; this script is
provenance for how they were produced, not something any test or harness re-runs. Re-run it
only if the fixture set itself is deliberately revised.

**Selection.** V17: "a representative subset ... spanning all document types, both form
types, and both clean-scan and photograph-like conditions -- deliberately not a fixed
percentage." One clean (non-adversarial) case per form schema -- KYC (4 documents: id_proof,
address_proof, income_document, academic_record) and Insurance (the same 4 plus the nominee
declaration) -- with every one of that case's documents rendered in BOTH conditions. This is
the smallest set that still covers the full matrix V17 asks for: every document type x every
form type x both conditions, at least once. Commit 5's full dataset build is a separate,
later step; this script does not depend on it and produces its own fixed-seed cases directly
from Commit 1-3's building blocks.

**Ground truth parity.** Each manifest entry carries the exact ground_truth dict of the case
its source document came from -- an OCR-rendered image never has different ground truth
than the text document it was rendered from, since rendering condition has no bearing on
what the identity's own field values are (eval.dataset.case_builder computes ground truth
from document *composition*, never from how a document is rendered -- see that module's own
docstring). test_generate_ocr_subset.py checks this by rebuilding the same two cases
independently and comparing, not by trusting this script's own bookkeeping.
"""

import hashlib
import json
import random
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from app.config.form_schema import load_form_schemas
from eval.dataset.case_builder import Case, build_clean_case
from eval.dataset.ocr_rendering import render_clean_scan_image, render_photo_like_image

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "phase6_ocr"
MANIFEST_FILENAME = "manifest.json"

REFERENCE_DATE = date(2026, 7, 29)
KYC_CASE_SEED = 6001
INSURANCE_CASE_SEED = 6002

CLEAN_SCAN = "clean_scan"
PHOTO_LIKE = "photo_like"


@dataclass(frozen=True)
class OcrFixture:
    source_case_id: str
    source_document_id: str
    document_type: str
    form_schema_id: str
    condition: str
    image_file: str
    ground_truth: dict[str, str | None]


def build_representative_cases() -> list[Case]:
    schemas = {schema.id: schema for schema in load_form_schemas()}
    kyc_case = build_clean_case(
        random.Random(KYC_CASE_SEED), "phase6_ocr_kyc", schemas["kyc_account_opening"], REFERENCE_DATE
    )
    insurance_case = build_clean_case(
        random.Random(INSURANCE_CASE_SEED),
        "phase6_ocr_insurance",
        schemas["insurance_policy_application"],
        REFERENCE_DATE,
    )
    return [kyc_case, insurance_case]


def _photo_rng_for(case_id: str, document_id: str) -> random.Random:
    # Deterministic per (case, document) via an explicit hash -- not one shared rng stream
    # advanced across every fixture -- so adding, removing, or reordering documents/cases
    # later cannot shift any *other* fixture's degradation parameters. Same hashing pattern
    # eval.harness.embedding_cache already uses for its own cache keys.
    digest = hashlib.sha256(f"{case_id}:{document_id}".encode()).digest()
    seed = int.from_bytes(digest[:8], "big")
    return random.Random(seed)


def generate(output_dir: Path) -> list[OcrFixture]:
    output_dir.mkdir(parents=True, exist_ok=True)
    fixtures: list[OcrFixture] = []

    for case in build_representative_cases():
        for document in case.documents:
            clean_image = render_clean_scan_image(document.text)
            clean_filename = f"{document.document_id}__{CLEAN_SCAN}.png"
            clean_image.save(output_dir / clean_filename)
            fixtures.append(
                OcrFixture(
                    source_case_id=case.case_id,
                    source_document_id=document.document_id,
                    document_type=document.document_type,
                    form_schema_id=case.form_schema_id,
                    condition=CLEAN_SCAN,
                    image_file=clean_filename,
                    ground_truth=case.ground_truth,
                )
            )

            photo_rng = _photo_rng_for(case.case_id, document.document_id)
            photo_image = render_photo_like_image(document.text, photo_rng)
            photo_filename = f"{document.document_id}__{PHOTO_LIKE}.jpg"
            photo_image.save(output_dir / photo_filename)
            fixtures.append(
                OcrFixture(
                    source_case_id=case.case_id,
                    source_document_id=document.document_id,
                    document_type=document.document_type,
                    form_schema_id=case.form_schema_id,
                    condition=PHOTO_LIKE,
                    image_file=photo_filename,
                    ground_truth=case.ground_truth,
                )
            )

    return fixtures


def main() -> None:
    fixtures = generate(FIXTURES_DIR)
    manifest_path = FIXTURES_DIR / MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps([asdict(fixture) for fixture in fixtures], indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Wrote {len(fixtures)} images and {manifest_path}")


if __name__ == "__main__":
    main()
