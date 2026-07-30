"""Unit tests for eval.harness.measure_ocr_accuracy's pure helper logic (Phase 7 Commit 6).

Per this commit's own scope: no live LLM calls, and no real OCR/Tesseract dependency here
either -- build_document_image_index/resolve_image_path/select_ocr_cases are pure functions
of already-loaded manifest/dataset data, exercised with small synthetic fixtures. The
run_graph-calling path (_evaluate_field, run_matrix_for_ocr, main) and the real ingestion
path (ingest_case_documents_from_images, which genuinely needs Tesseract) are not exercised
here -- consistent with the project's existing tests/conftest.py skip convention for
anything that needs the real binary, and with this commit's own instruction to avoid testing
live model interactions.
"""

import pytest

from eval.harness.measure_ocr_accuracy import (
    ManifestEntry,
    OcrImageNotFoundError,
    build_document_image_index,
    resolve_image_path,
    select_ocr_cases,
)

_MANIFEST = [
    ManifestEntry(
        source_case_id="phase6_ocr_kyc",
        source_document_id="phase6_ocr_kyc__id_proof",
        condition="clean_scan",
        image_file="phase6_ocr_kyc__id_proof__clean_scan.png",
    ),
    ManifestEntry(
        source_case_id="phase6_ocr_kyc",
        source_document_id="phase6_ocr_kyc__id_proof",
        condition="photo_like",
        image_file="phase6_ocr_kyc__id_proof__photo_like.jpg",
    ),
    ManifestEntry(
        source_case_id="phase6_ocr_insurance",
        source_document_id="phase6_ocr_insurance__nominee_declaration",
        condition="clean_scan",
        image_file="phase6_ocr_insurance__nominee_declaration__clean_scan.png",
    ),
]


# ---------------------------------------------------------------------------
# build_document_image_index / resolve_image_path
# ---------------------------------------------------------------------------


def test_build_document_image_index_keys_by_case_document_condition() -> None:
    index = build_document_image_index(_MANIFEST)
    assert index[("phase6_ocr_kyc", "phase6_ocr_kyc__id_proof", "clean_scan")] == (
        "phase6_ocr_kyc__id_proof__clean_scan.png"
    )
    assert index[("phase6_ocr_kyc", "phase6_ocr_kyc__id_proof", "photo_like")] == (
        "phase6_ocr_kyc__id_proof__photo_like.jpg"
    )
    assert len(index) == 3


def test_resolve_image_path_returns_a_path_under_the_fixtures_directory() -> None:
    index = build_document_image_index(_MANIFEST)
    path = resolve_image_path(index, "phase6_ocr_kyc", "phase6_ocr_kyc__id_proof", "clean_scan")
    assert path.name == "phase6_ocr_kyc__id_proof__clean_scan.png"
    assert path.parent.name == "phase6_ocr"


@pytest.mark.parametrize(
    ("case_id", "document_id", "condition"),
    [
        ("phase6_ocr_kyc", "phase6_ocr_kyc__id_proof", "does_not_exist"),
        ("phase6_ocr_kyc", "no_such_document", "clean_scan"),
        ("no_such_case", "phase6_ocr_kyc__id_proof", "clean_scan"),
    ],
)
def test_resolve_image_path_raises_loudly_for_an_unknown_combination(
    case_id: str, document_id: str, condition: str
) -> None:
    index = build_document_image_index(_MANIFEST)
    with pytest.raises(OcrImageNotFoundError):
        resolve_image_path(index, case_id, document_id, condition)


def test_resolve_image_path_distinguishes_conditions_for_the_same_document() -> None:
    """A common bug shape for this kind of lookup: accidentally keying only on
    (case_id, document_id) and returning whichever condition happened to be inserted last.
    Both conditions for the same document must resolve independently and correctly."""
    index = build_document_image_index(_MANIFEST)
    clean = resolve_image_path(index, "phase6_ocr_kyc", "phase6_ocr_kyc__id_proof", "clean_scan")
    photo = resolve_image_path(index, "phase6_ocr_kyc", "phase6_ocr_kyc__id_proof", "photo_like")
    assert clean != photo
    assert clean.suffix == ".png"
    assert photo.suffix == ".jpg"


# ---------------------------------------------------------------------------
# select_ocr_cases
# ---------------------------------------------------------------------------


def _case(case_id: str) -> dict[str, object]:
    return {"case_id": case_id, "documents": []}


def test_select_ocr_cases_filters_and_preserves_requested_order() -> None:
    all_cases = [_case("insurance_clean_001"), _case("phase6_ocr_insurance"), _case("phase6_ocr_kyc"), _case("kyc_clean_001")]
    selected = select_ocr_cases(all_cases, case_ids=("phase6_ocr_kyc", "phase6_ocr_insurance"))
    assert [c["case_id"] for c in selected] == ["phase6_ocr_kyc", "phase6_ocr_insurance"]


def test_select_ocr_cases_raises_loudly_when_a_requested_case_id_is_missing() -> None:
    all_cases = [_case("phase6_ocr_kyc")]
    with pytest.raises(OcrImageNotFoundError):
        select_ocr_cases(all_cases, case_ids=("phase6_ocr_kyc", "phase6_ocr_insurance"))
